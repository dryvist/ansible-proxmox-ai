#!/usr/bin/env python3
"""Report how many times each hermes_agent pinned-source patch matches upstream.

The tests run the role's regexps against PINNED_* snippets copied into
conftest.py. That proves a patch does what it claims, not that its anchor still
exists — so when upstream moves, the snippets keep the tests green while the
patches quietly match nothing on the guest. That is not theoretical: seven
patches were dead this way, and patches_verify.yml runs AFTER the patch tasks,
so the converge would mutate a live guest and fail only afterwards.

Run this when hermes_agent_version moves, before converging:

    python3 scripts/verify-pinned-patches.py            # the pinned version
    python3 scripts/verify-pinned-patches.py 2026.8.13  # plus another release

Every patch should report its declared expected count (one unless the task
explicitly declares otherwise). Patches are applied to the in-memory source in
role order after counting, because later patches can intentionally anchor on an
earlier patch's output just as they do during a real converge. blockinfile
blocks are inserted the same way.

Readback asserts in patches*.yml are checked in role order too. Each `that`
item is evaluated with Jinja, as Ansible evaluates it, against the source its
slurp read at that point: slurp content is base64 of the upstream text, exactly
as Ansible returns it. A false item is reported as file:line. An item that
references a variable this check does not provide is listed as skipped, never
passed.
"""

from __future__ import annotations

import base64
import io
import re
import sys
import tarfile
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jinja2 import (
    DebugUndefined,
    Environment,
    StrictUndefined,
    TemplateError,
    UndefinedError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "hermes_agent"))

from _role_files import role_defaults  # noqa: E402

ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
TARBALL = "https://github.com/NousResearch/hermes-agent/archive/refs/tags/v{}.tar.gz"
_INCLUDE_KEYS = ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks")


@dataclass
class ReadbackReport:
    checked: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def upstream_sources(version: str) -> dict[str, str]:
    """Return {path-within-repo: text} for one upstream release."""
    with urllib.request.urlopen(TARBALL.format(version)) as response:  # noqa: S310
        raw = response.read()
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            # Strip the leading "hermes-agent-<version>/" component.
            sources[member.name.split("/", 1)[1]] = handle.read().decode("utf-8")
    return sources


def execution_order(tasks_dir: Path, entry: str = "main.yml") -> Iterator[tuple[Path, dict, yaml.Node]]:
    """Yield (file, task, yaml node) in the order Ansible runs them.

    include_tasks/import_tasks entries expand in place, so a check sees exactly
    the patches and slurps that precede it in the role.
    """
    path = tasks_dir / entry
    text = path.read_text()
    tasks = yaml.safe_load(text) or []
    root = yaml.compose(text)
    nodes = root.value if isinstance(root, yaml.SequenceNode) else []
    for task, node in zip(tasks, nodes):
        if not isinstance(task, dict):
            continue
        target = next(
            (
                task[key]
                for key in _INCLUDE_KEYS
                if isinstance(task.get(key), str) and task[key].endswith(".yml")
            ),
            None,
        )
        if target is None:
            yield path, task, node
        else:
            yield from execution_order(tasks_dir, target)


def _mapping_value(node: yaml.MappingNode, key: str) -> yaml.Node | None:
    for node_key, value in node.value:
        if isinstance(node_key, yaml.ScalarNode) and node_key.value == key:
            return value
    return None


def _upstream_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"\}\}\s*/(.+)$", value.strip())
    return match.group(1) if match else None


def _encoded(text: str | None) -> dict:
    """One slurp result as Ansible returns it: base64 content, absent when unknown."""
    if text is None:
        return {}
    return {"content": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def _slurp_result(task: dict, sources: dict[str, str]) -> tuple[str, dict] | None:
    """For a slurp task, return (register name, result as of now)."""
    config = task.get("ansible.builtin.slurp")
    register = task.get("register")
    if not isinstance(config, dict) or not isinstance(register, str):
        return None
    src = config.get("src")
    loop = task.get("loop", task.get("with_items"))
    if isinstance(loop, list):
        if isinstance(src, str) and re.fullmatch(r"\s*{{\s*item\s*}}\s*", src):
            paths = [_upstream_path(item) for item in loop]
        else:
            paths = [_upstream_path(src) for _ in loop]
        return register, {
            "results": [_encoded(sources.get(path) if path else None) for path in paths]
        }
    path = _upstream_path(src)
    return register, _encoded(sources.get(path) if path else None)


def _environment(undefined: type) -> Environment:
    env = Environment(autoescape=False, undefined=undefined)
    env.filters["b64decode"] = lambda value: base64.b64decode(str(value)).decode("utf-8")
    env.filters["bool"] = lambda value: (
        value
        if isinstance(value, bool)
        else str(value).lower() in {"1", "true", "yes", "on", "y", "t"}
    )

    def regex_search(value: object, pattern: str) -> str | None:
        match = re.search(pattern, str(value))
        if match is None:
            return None
        return match.group(1) if match.groups() else match.group(0)

    env.filters["regex_search"] = regex_search
    return env


def _unwrap(expression: str) -> str:
    expression = expression.strip()
    if expression.startswith("{{") and expression.endswith("}}"):
        return expression[2:-2].strip()
    return expression


def _block_index(config: dict, lines: list[str]) -> int:
    """Where blockinfile puts its block: beside the last matching line, else at EOF."""
    if config.get("insertbefore") == "BOF":
        return 0
    if "insertbefore" in config and config["insertbefore"] != "EOF":
        key, offset = "insertbefore", 0
    elif "insertafter" in config and config["insertafter"] != "EOF":
        key, offset = "insertafter", 1
    else:
        return len(lines)
    matches = [index for index, line in enumerate(lines) if re.search(config[key], line)]
    return matches[-1] + offset if matches else len(lines)


def _apply_block(task: dict, sources: dict[str, str], environment: Environment) -> None:
    """Insert a blockinfile block (between its markers) into the in-memory source."""
    config = task["ansible.builtin.blockinfile"]
    relative = str(config.get("path", "")).split("}}/")[-1]
    source = sources.get(relative)
    if source is None:
        print(f"  n/a  {task['name']}  ({relative} not in this release)")
        return
    try:
        block = environment.from_string(config["block"]).render(**task.get("vars", {}))
    except UndefinedError:
        # A filter such as `| int` cannot take an unresolved role var; keep the raw text.
        block = config["block"]
    marker = config.get("marker", "# {mark} ANSIBLE MANAGED BLOCK")
    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    body = [marker.replace("{mark}", "BEGIN"), *block.splitlines(), marker.replace("{mark}", "END")]
    index = _block_index(config, lines)
    lines[index:index] = [f"{line}\n" for line in body]
    sources[relative] = "".join(lines)
    print(f"  block  {task['name']}")


def _apply_patch(task: dict, sources: dict[str, str], environment: Environment) -> int:
    """Count one replace patch against its upstream file, then apply it in memory.

    Returns 1 when the match count differs from the declared expectation.
    """
    name = task["name"]
    config = task["ansible.builtin.replace"]
    expected = int(
        task.get("vars", {}).get("hermes_agent_pinned_patch_expected_matches", 1)
    )
    relative = str(config.get("path", "")).split("}}/")[-1]
    source = sources.get(relative)
    if source is None:
        print(f"  n/a  {name}  ({relative} not in this release)")
        return 0
    count = len(re.findall(config["regexp"], source, flags=re.MULTILINE))
    print(f"  {count:>3}/{expected:<3}  {name}")
    if count:
        try:
            replacement = environment.from_string(config["replace"]).render(
                **task.get("vars", {})
            )
        except UndefinedError:
            # Matching is already proven. Preserve unresolved role vars
            # literally when a later patch does not depend on them.
            replacement = config["replace"]
        sources[relative] = re.sub(
            config["regexp"], replacement, source, flags=re.MULTILINE
        )
    return int(count != expected)


def _check_readback_assert(
    task: dict,
    node: yaml.Node,
    location_path: str,
    registers: dict[str, dict],
    report: ReadbackReport,
) -> None:
    config = task.get("ansible.builtin.assert")
    if not isinstance(config, dict) or not isinstance(node, yaml.MappingNode):
        return
    assert_node = _mapping_value(node, "ansible.builtin.assert")
    if not isinstance(assert_node, yaml.MappingNode):
        return
    checks = config.get("that", [])
    if isinstance(checks, str):
        checks = [checks]
    that_node = _mapping_value(assert_node, "that")
    if isinstance(that_node, yaml.SequenceNode):
        check_nodes = that_node.value
    elif that_node is not None:
        check_nodes = [that_node]
    else:
        check_nodes = []
    environment = _environment(StrictUndefined)
    scope: dict = dict(registers)
    task_vars = task.get("vars")
    if not isinstance(task_vars, dict):
        task_vars = {}
    for name, value in task_vars.items():
        try:
            scope[name] = environment.from_string(str(value)).render(**scope)
        except TemplateError:
            # Left unset: each check that reads it is reported as skipped below.
            pass
    for check, check_node in zip(checks, check_nodes):
        location = f"{location_path}:{check_node.start_mark.line + 1}"
        expression = _unwrap(str(check))
        try:
            outcome = bool(environment.compile_expression(expression, undefined_to_none=False)(**scope))
        except UndefinedError as exc:
            report.skipped.append((location, f"unresolved in this check: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 - any other error fails the converge too
            report.checked += 1
            report.failures.append((location, f"{expression} raised {type(exc).__name__}: {exc}"))
            continue
        report.checked += 1
        if not outcome:
            report.failures.append((location, expression))


def verify_role(
    role_root: Path, sources: dict[str, str], environment: Environment
) -> tuple[int, ReadbackReport]:
    """Apply every patch in role order and evaluate each readback assert where it runs."""
    unexpected = 0
    registers: dict[str, dict] = {}
    report = ReadbackReport()
    repo_root = role_root.parents[1]
    for path, task, node in execution_order(role_root / "tasks"):
        if "ansible.builtin.replace" in task:
            unexpected += _apply_patch(task, sources, environment)
        elif "ansible.builtin.blockinfile" in task:
            _apply_block(task, sources, environment)
        elif "ansible.builtin.slurp" in task:
            result = _slurp_result(task, sources)
            if result is not None:
                registers[result[0]] = result[1]
        elif "ansible.builtin.assert" in task and path.name.startswith("patches"):
            _check_readback_assert(
                task,
                node,
                path.relative_to(repo_root).as_posix(),
                registers,
                report,
            )
    return unexpected, report


def main(versions: list[str]) -> int:
    environment = Environment(autoescape=False, undefined=DebugUndefined)
    unexpected = 0
    readback_failures = 0
    for version in versions:
        print(f"== hermes-agent v{version}")
        version_unexpected, report = verify_role(
            ROLE_ROOT, upstream_sources(version), environment
        )
        unexpected += version_unexpected
        readback_failures += len(report.failures)
        print(
            f"readback asserts: {report.checked - len(report.failures)} passed, "
            f"{len(report.failures)} failed, {len(report.skipped)} skipped"
        )
        for location, expression in report.failures:
            print(f"  FAIL {location}: {expression}")
        for location, reason in report.skipped:
            print(f"  skipped {location}: {reason}")
    print(f"\nunexpected match counts: {unexpected}")
    print(f"failing readback asserts: {readback_failures}")
    return 1 if unexpected or readback_failures else 0


if __name__ == "__main__":
    pinned = role_defaults(ROLE_ROOT)["hermes_agent_version"]
    sys.exit(main(sys.argv[1:] or [pinned]))
