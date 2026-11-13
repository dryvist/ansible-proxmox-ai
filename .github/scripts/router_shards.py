"""Weight-aware packing for the LLM Router contract matrix."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TypedDict

import yaml

MAX_ROUTER_SHARDS = 16
MAX_ROUTER_SHARD_NAME = 60
ROUTER_WEIGHTS_FILE = Path(__file__).resolve().parents[1] / "router-contract-weights.yml"
ROUTER_CONTRACTS_FILE = Path(__file__).resolve().parents[1] / "router-contracts.yml"
REPO_ROOT = ROUTER_WEIGHTS_FILE.parent.parent


class RouterShard(TypedDict):
    name: str
    playbooks: str


class RouterShardBuilder(TypedDict):
    units: list[tuple[str, str, list[str], int]]
    playbooks: list[str]
    weight: int


def load_router_weights() -> dict[str, int]:
    try:
        weights = yaml.safe_load(ROUTER_WEIGHTS_FILE.read_text(encoding="utf-8")).get("weights", {})
        return {str(path): int(weight) for path, weight in weights.items()}
    except (AttributeError, OSError, TypeError, ValueError, yaml.YAMLError):
        return {}


def _valid_contracts(value: object) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("router contracts must be a non-empty list")
    contracts: list[list[str]] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, list) or not entry:
            raise ValueError("router contract entries must be non-empty lists")
        if any(not isinstance(path, str) or not path.startswith("tests/llm_router/") for path in entry):
            raise ValueError("router contract paths must name llm_router tests")
        if seen.intersection(entry):
            raise ValueError("router contract paths must be unique")
        seen.update(entry)
        contracts.append(entry)
    return contracts


def _scan_router_playbooks() -> list[list[str]]:
    contracts: list[list[str]] = []
    for path in sorted((REPO_ROOT / "tests/llm_router").glob("*.yml")):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(document, list) and any(
            isinstance(item, dict) and {"hosts", "import_playbook", "ansible.builtin.import_playbook"} & set(item)
            for item in document
        ):
            contracts.append([path.relative_to(REPO_ROOT).as_posix()])
    return contracts


def load_router_contracts() -> list[list[str]]:
    try:
        config = yaml.safe_load(ROUTER_CONTRACTS_FILE.read_text(encoding="utf-8"))
        return _valid_contracts(config["contracts"])
    except (AttributeError, KeyError, OSError, TypeError, ValueError, yaml.YAMLError):
        return _scan_router_playbooks()


def git_file_at_ref(ref: str, path: str) -> str | None:
    if not ref:
        raise ValueError("base and head refs are required for defaults comparison")
    checked = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], capture_output=True, text=True, check=False,
    )
    if checked.returncode:
        raise ValueError(f"could not resolve git ref {ref}")
    spec = f"{ref}:{path}"
    exists = subprocess.run(["git", "cat-file", "-e", spec], capture_output=True, check=False)
    if exists.returncode:
        return None
    return subprocess.check_output(["git", "show", spec], text=True)


def router_default_changed_keys(path: str, base_ref: str, head_ref: str) -> set[str] | None:
    try:
        versions = [git_file_at_ref(ref, path) for ref in (base_ref, head_ref)]
        values = []
        for source in versions:
            parsed = yaml.safe_load(source) if source is not None else {}
            if parsed is None:
                parsed = {}
            if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
                return None
            values.append(parsed)
        old, new = values
        return {key for key in old.keys() | new.keys() if old.get(key, _MISSING) != new.get(key, _MISSING)}
    except (OSError, subprocess.SubprocessError, TypeError, ValueError, yaml.YAMLError):
        return None


_MISSING = object()
_JINJA = re.compile(r"{{.*?}}|{%.*?%}", re.DOTALL)
_JINJA_NAME = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_IMPORT_KEYS = {"import_playbook", "import_tasks", "include_tasks"}


def _walk_yaml(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_yaml(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_yaml(child)


def _playbook_variables(document: object) -> dict[str, str]:
    variables: dict[str, str] = {}
    for key, value in _walk_yaml(document):
        if key == "vars" and isinstance(value, dict):
            variables.update({name: item for name, item in value.items() if isinstance(item, str)})
    return variables


def _expand_import(target: str, playbook: str, variables: dict[str, str]) -> str:
    values = {"playbook_dir": Path(playbook).parent.as_posix(), **variables}
    for _ in range(5):
        expanded = _JINJA_NAME.sub(lambda match: values.get(match.group(1), match.group(0)), target)
        if expanded == target:
            break
        target = expanded
    return target


def _import_targets(document: object) -> list[str]:
    targets = []
    for key, value in _walk_yaml(document):
        if isinstance(key, str) and key.split(".")[-1] in _IMPORT_KEYS:
            if isinstance(value, dict):
                value = value.get("file")
            if not isinstance(value, str):
                raise ValueError("router contract import path is not a string")
            targets.append(value)
    return targets


def _candidate_import(current: str, playbook: str, target: str) -> list[Path] | None:
    expanded = _expand_import(target, playbook, {})
    if "{{" in expanded or "{%" in expanded:
        prefix = expanded.split("{{", 1)[0].split("{%", 1)[0].rstrip("/")
        if not prefix:
            return None
        candidates = [REPO_ROOT / prefix, REPO_ROOT / Path(current).parent / prefix,
                      REPO_ROOT / Path(playbook).parent / prefix]
        directory = next((path for path in candidates if path.is_dir()), None)
        if directory is None:
            return None
        return sorted(path for path in directory.rglob("*") if path.suffix in {".yml", ".yaml"})

    candidates = [REPO_ROOT / Path(current).parent / expanded,
                  REPO_ROOT / Path(playbook).parent / expanded,
                  REPO_ROOT / expanded]
    found = next((path for path in candidates if path.is_file()), None)
    return [found] if found else None


def _contract_sources(playbook: str, changed_keys: set[str]) -> tuple[list[tuple[str, str]], bool]:
    root = REPO_ROOT / playbook
    try:
        root_document = yaml.safe_load(root.read_text(encoding="utf-8"))
        variables = _playbook_variables(root_document)
        for name, value in list(variables.items()):
            variables[name] = _expand_import(value, playbook, variables)
        pending = [root]
        seen: set[Path] = set()
        sources: list[tuple[str, str]] = []
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            content = path.read_text(encoding="utf-8")
            relative = path.relative_to(REPO_ROOT).as_posix()
            sources.append((relative, content))
            document = yaml.safe_load(content)
            variables.update(_playbook_variables(document))
            for name, value in list(variables.items()):
                variables[name] = _expand_import(value, playbook, variables)
            for target in _import_targets(document):
                expanded = _expand_import(target, playbook, variables)
                imported = _candidate_import(relative, playbook, expanded)
                if imported is None:
                    if any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", target)
                           for key in changed_keys):
                        return sources, True
                    continue
                pending.extend(imported)
        return sources, False
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return [], True


def _registered_enabled_vars() -> set[str] | None:
    variables: set[str] = set()
    try:
        for path in (REPO_ROOT / "llm-models.d").rglob("*.yml"):
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            variables.update(value for key, value in _walk_yaml(document)
                             if key == "enabled_var" and isinstance(value, str))
        return variables
    except (OSError, yaml.YAMLError):
        return None


def _unresolved_jinja_reference(
    content: str, changed_keys: set[str], enabled_vars: set[str] | None
) -> bool:
    for expression in _JINJA.findall(content):
        lookup = re.search(
            r"\blookup\s*\(\s*['\"](?:ansible\.builtin\.)?vars['\"]\s*,\s*([^,]+)", expression,
        )
        if lookup and not lookup.group(1).strip().startswith(("'", '"')):
            argument = lookup.group(1).strip()
            if "model.enabled_var" in argument:
                if enabled_vars is None or changed_keys.intersection(enabled_vars):
                    return True
            else:
                return True
        if re.search(r"\bvars\s*\[\s*(?!['\"])", expression):
            return True
        if re.search(r"\bhostvars\s*\[[^\]]+\]\s*\[\s*(?!['\"])", expression):
            return True
        if re.search(r"\bvars\s*\|\s*(?:dict2items|items)\b", expression):
            return True
    return False


def router_default_playbooks(
    defaults_path: str, changed_keys: set[str], matrix: list[list[str]]
) -> tuple[list[list[str]], bool]:
    selected: list[list[str]] = []
    filename = Path(defaults_path).name
    enabled_vars = _registered_enabled_vars() if changed_keys else set()
    key_patterns = [re.compile(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])")
                    for key in changed_keys]
    for entry in matrix:
        affected = False
        for playbook in entry:
            sources, unresolved = _contract_sources(playbook, changed_keys)
            if unresolved:
                return [], True
            for _, content in sources:
                if defaults_path in content or filename in content:
                    affected = True
                if any(pattern.search(content) for pattern in key_patterns):
                    affected = True
                if changed_keys and _unresolved_jinja_reference(content, changed_keys, enabled_vars):
                    return [], True
        if affected:
            selected.append(entry)
    return selected, False


ROUTER_WEIGHTS = load_router_weights()


def router_weight(playbook: str) -> int:
    return ROUTER_WEIGHTS.get(playbook, 1)


def router_topic(playbook: str) -> str:
    stem = re.sub(r"^(?:test|verify)_", "", Path(playbook).stem)
    return stem.split("_", 1)[0]


def router_shard_target(matrix: list[list[str]]) -> int:
    playbooks = [playbook for entry in matrix for playbook in entry]
    shard_count = min(MAX_ROUTER_SHARDS, len(playbooks))
    if not shard_count:
        return 1
    total_weight = sum(router_weight(playbook) for playbook in playbooks)
    return max(1, (total_weight + shard_count - 1) // shard_count)


def router_shards(matrix: list[list[str]], shard_target: int | None = None) -> list[RouterShard]:
    playbooks = sorted(playbook for entry in matrix for playbook in entry)
    if not playbooks:
        return []
    if len(playbooks) <= 6:
        topics = ", ".join(sorted({router_topic(playbook) for playbook in playbooks}))
        name = topics[:MAX_ROUTER_SHARD_NAME - 1].rstrip() + "…" if len(topics) > MAX_ROUTER_SHARD_NAME else topics
        return [{"name": name, "playbooks": " ".join(playbooks)}]

    target = max(1, shard_target if shard_target is not None else router_shard_target(matrix))
    total_weight = sum(router_weight(playbook) for playbook in playbooks)
    shard_count = min(MAX_ROUTER_SHARDS, len(playbooks), (total_weight + target - 1) // target)
    by_topic: dict[str, list[str]] = {}
    for playbook in playbooks:
        by_topic.setdefault(router_topic(playbook), []).append(playbook)

    units: list[tuple[str, str, list[str], int]] = []
    for topic, paths in sorted(by_topic.items()):
        chunks: list[tuple[list[str], int]] = []
        chunk: list[str] = []
        chunk_weight = 0
        for playbook in paths:
            weight = router_weight(playbook)
            if chunk and chunk_weight + weight > target:
                chunks.append((chunk, chunk_weight))
                chunk = []
                chunk_weight = 0
            chunk.append(playbook)
            chunk_weight += weight
        if chunk:
            chunks.append((chunk, chunk_weight))

        chunk_count = len(chunks)
        for index, (chunk_paths, weight) in enumerate(chunks):
            label = f"{topic} {index + 1}/{chunk_count}" if chunk_count > 1 else topic
            units.append((topic, label, chunk_paths, weight))
    units.sort(key=lambda unit: (-unit[3], unit[1]))

    shards: list[RouterShardBuilder] = [
        {"units": [], "playbooks": [], "weight": 0} for _ in range(shard_count)
    ]
    for unit in units:
        topic = unit[0]
        candidates = [shard for shard in shards
                      if all(existing[0] != topic for existing in shard["units"])]
        if not candidates:
            candidates = shards
        shard = min(candidates, key=lambda item: (item["weight"],
                                                   sorted(existing[1] for existing in item["units"])))
        shard["units"].append(unit)
        shard["playbooks"].extend(unit[2])
        shard["weight"] += unit[3]

    result: list[RouterShard] = []
    used_names: set[str] = set()
    for shard in shards:
        if not shard["units"]:
            continue
        topics = ", ".join(sorted(unit[1] for unit in shard["units"]))
        name = topics[:MAX_ROUTER_SHARD_NAME - 1].rstrip() + "…" if len(topics) > MAX_ROUTER_SHARD_NAME else topics
        if name in used_names:
            suffix = "… 2"
            name = name[:MAX_ROUTER_SHARD_NAME - len(suffix)] + suffix
            suffix_index = 3
            while name in used_names:
                suffix = f"… {suffix_index}"
                name = name[:MAX_ROUTER_SHARD_NAME - len(suffix)] + suffix
                suffix_index += 1
        used_names.add(name)
        result.append({"name": name, "playbooks": " ".join(sorted(shard["playbooks"]))})
    return sorted(result, key=lambda shard: shard["name"])
