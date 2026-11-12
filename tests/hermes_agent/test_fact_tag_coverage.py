"""Fail when a component tag reaches a task that reads a fact its producer
does not set under that same tag.

The component-tag narrowing (test_component_tag_narrowing.py) splits the
converge into per-component task selections. A fact set in one component but
read in another makes the reading component's scoped run fail on an
undefined variable — untagged runs never notice, because there every task
runs. This walks the same static tree the selection resolves (imports in
place, dynamic includes and foreign roles opaque, block/rescue/always
flattened) and requires every task reading a role-set fact to run only
under component tags the task setting the fact also carries. Producers
living ABOVE the role are covered the same way: the play-level loaders in
playbooks/agents.yml's pre_tasks publish facts (a public include_role makes
the included role's defaults play-scoped), and a task reading one of those
through an unguarded chain of role defaults must run only under component
tags its loader also carries. References a reader guards itself —
`| default(...)` and `is defined` — do not require coverage: they stay
defined by design in a scoped run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from _role_files import (
    _task_items,
    include_file,
    role_defaults,
    task_tags,
    template_text,
    walk_tagged_tasks,
)

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"
PLAYBOOK = REPO / "playbooks" / "agents.yml"
# The component tags every include in tasks/main.yml declares statically
# (see test_entry_point_tags.COMPONENT_TAGS — duplicated here so a new
# component must be added to both, the same way a missed tag fails).
COMPONENT_TAGS = (
    "hermes_runtime_install",
    "hermes_patches",
    "hermes_profiles_env",
    "hermes_content_scripts",
    "hermes_crons",
    "hermes_units_gateways",
    "hermes_agent_logging",
    "hermes_delivery_probe",
)

# A reader that pipes the fact into default(...) or tests it with
# `is (not) defined` stays defined in a scoped run on its own.
_DEFAULT_GUARD = re.compile(r"\s*(?:\.\w+)*\s*\|\s*default\(")
_DEFINED_GUARD = re.compile(r"\s+is\s+(?:not\s+)?defined\b")


def _walked() -> list[tuple[dict, tuple[str, ...], str]]:
    """Every attributable task in the static tree as (task, effective tags,
    source file), in execution order — the shared traversal, skipping the
    opaque single-listing entries (dynamic includes, foreign import_role)
    whose tasks carry no attributable facts."""
    entries = walk_tagged_tasks(
        yaml.safe_load((TASKS / "main.yml").read_text()) or [],
        TASKS, (), "main.yml", self_tasks_dir=TASKS,
    )
    return [(task, effective, source) for task, effective, source, opaque in entries
            if not opaque]


def _fact_names(task: dict, defaults: dict) -> set[str]:
    """Facts a task sets: set_fact keys (resolving the catalog loop's
    `{{ item.key }}` against its loop source) and register names."""
    names: set[str] = set()
    set_fact = task.get("ansible.builtin.set_fact")
    if isinstance(set_fact, dict):
        for key in set_fact:
            if key == "cacheable":
                continue
            if re.fullmatch(r"\{\{\s*item\.key\s*\}\}", str(key)):
                loop = re.search(r"(\w+)\s*\|\s*dict2items", str(task.get("loop", "")))
                source = defaults.get(loop.group(1)) if loop else None
                if isinstance(source, dict):
                    names.update(source)
                continue
            names.add(key)
    registered = task.get("register")
    if isinstance(registered, str):
        names.add(registered)
    return names


def _reads_text(task: dict) -> str:
    """Everything the task evaluates: its own fields plus the rendered body
    of any role template it deploys (the template's Jinja includes spliced
    in, like the narrowing tests splice task-file imports)."""
    text = yaml.dump(task, default_flow_style=False)
    for src in re.findall(r"src:\s*([\w.\-/]+\.j2)\b", text):
        name = Path(src).name
        if (ROLE / "templates" / name).is_file():
            text += template_text(ROLE, name)
    return text


def _unguarded_reads(text: str, name: str) -> bool:
    """True when the task text references the fact outside a self-guard."""
    for match in re.finditer(r"(?<![\w.])" + re.escape(name) + r"\b", text):
        rest = text[match.end():]
        if _DEFAULT_GUARD.match(rest) or _DEFINED_GUARD.match(rest):
            continue
        return True
    return False


def _runs_under(tags: tuple[str, ...], tag: str) -> bool:
    return "always" in tags or tag in tags


# --- Playbook-level producers -------------------------------------------------
#
# The play-level loaders (agents.yml pre_tasks, import_tasks of
# playbooks/tasks/load_*.yml) publish facts ABOVE the role: include_role with
# public: true makes the included role's defaults play-scoped, and set_fact /
# include_vars anywhere in the loaded chain are host facts anyway. A task
# reading one of those facts — directly or through an unguarded chain of role
# defaults, which is how the goal-judge timeout reaches the patch tasks —
# must run only under component tags its loader also carries.


def _resolve_role_root(name: str) -> Path | None:
    """Where a role's content lives: this repo's roles/ first, then the
    installed collections (requirements.yml), the same lookup Ansible does."""
    direct = REPO / "roles" / name
    if direct.is_dir():
        return direct
    parts = name.split(".")
    if len(parts) == 3:  # FQCN: namespace.collection.role
        installed = (
            Path.home()
            / ".ansible/collections/ansible_collections"
            / parts[0] / parts[1] / "roles" / parts[2]
        )
        if installed.is_dir():
            return installed
    return None


def _merged_keys(path: Path) -> list[str]:
    """Top-level keys of a YAML file, or of defaults/main/ split files."""
    if (path / "main").is_dir():
        keys: list[str] = []
        for f in sorted((path / "main").glob("*.yml")):
            keys.extend(yaml.safe_load(f.read_text()) or {})
        return keys
    main = path / "main.yml"
    return list(yaml.safe_load(main.read_text()) or {}) if main.is_file() else []


def _include_vars_keys(task: dict, role_root: Path) -> set[str]:
    """Facts an include_vars sets: the loaded file's top-level keys. Only the
    static `file:` form resolves (a `dir:` load publishes the registry
    shards' names, which no role task reads by name); a path this static
    pass cannot render is skipped."""
    spec = task.get("ansible.builtin.include_vars")
    if not isinstance(spec, dict):
        return set()
    source = spec.get("file")
    if not isinstance(source, str):
        return set()
    rendered = source.replace("{{ role_path }}", str(role_root)).strip("'\"")
    if "{{" in rendered or not rendered.startswith(str(role_root)):
        return set()
    path = Path(rendered)
    if not path.is_file():
        return set()
    if path.suffix == ".json":
        loaded = json.loads(path.read_text())
    else:
        loaded = yaml.safe_load(path.read_text())
    return set(loaded) if isinstance(loaded, dict) else set()


def _published_facts(role_root: Path, tasks_from: str, public: bool,
                     visited: set[tuple[str, str]]) -> set[str]:
    """Facts running a role's tasks entry publishes at play scope: its
    defaults when the include is public, plus set_fact keys, registers and
    include_vars keys from its own chain (nested include_roles recursed,
    loop `{{ item.key }}` set_facts skipped as unresolvable)."""
    key = (str(role_root), tasks_from)
    if key in visited:
        return set()
    visited.add(key)
    facts: set[str] = set(_merged_keys(role_root / "defaults")) if public else set()
    entry = role_root / "tasks" / (
        tasks_from if tasks_from.endswith(".yml") else tasks_from + ".yml"
    )
    for task in _task_items(yaml.safe_load(entry.read_text()) or []):
        set_fact = task.get("ansible.builtin.set_fact")
        if isinstance(set_fact, dict):
            facts.update(
                name for name in set_fact
                if name != "cacheable" and "item." not in name
            )
        if isinstance(task.get("register"), str):
            facts.add(task["register"])
        facts |= _include_vars_keys(task, role_root)
        call = task.get("ansible.builtin.include_role")
        if isinstance(call, dict):
            nested = _resolve_role_root(call.get("name", ""))
            if nested is not None:
                facts |= _published_facts(
                    nested, call.get("tasks_from") or "main",
                    call.get("public") is True, visited,
                )
    return facts


def _play_producers() -> dict[str, tuple[tuple[str, ...], str]]:
    """Every fact the agents.yml pre_task loaders publish play-wide, mapped
    to the loader pre_task's declared tags and a source label naming the
    loader. The catalog loader must stay present: it is the producer whose
    missing hermes_patches tag this file exists to catch."""
    producers: dict[str, tuple[tuple[str, ...], str]] = {}
    for play in yaml.safe_load(PLAYBOOK.read_text()):
        for pre in play.get("pre_tasks") or []:
            target = include_file(pre, "ansible.builtin.import_tasks")
            if not (isinstance(target, str) and Path(target).name.startswith("load_")):
                continue
            tags = tuple(task_tags(pre))
            label = f"playbooks/{target}"
            for task in yaml.safe_load((REPO / "playbooks" / target).read_text()) or []:
                call = task.get("ansible.builtin.include_role")
                if not isinstance(call, dict):
                    continue
                role_root = _resolve_role_root(call.get("name", ""))
                if role_root is None:
                    continue
                source = f"{label} ({call['name']} via include_role)"
                for fact in _published_facts(
                    role_root, call.get("tasks_from") or "main",
                    call.get("public") is True, set(),
                ):
                    producers.setdefault(fact, (tags, source))
    assert "llm_model_catalog_models" in producers, (
        "agents.yml lost its catalog loader pre_task"
    )
    return producers


def _default_reference_closure(defaults: dict,
                               produced: set[str]) -> dict[str, set[str]]:
    """Produced facts reachable from each role default through UNGUARDED
    references in default values — rendering the default renders those
    facts. Same guard rule as the task-level check: a `| default(...)` or
    `is defined` reference stays defined in a scoped run and is not a read.
    Fixpoint iteration, so reference cycles converge."""
    candidates = set(defaults) | produced
    edges: dict[str, set[str]] = {}
    for name, value in defaults.items():
        if not isinstance(value, str):
            continue
        edges[name] = {
            c for c in candidates if c != name and _unguarded_reads(value, c)
        }
    closure = {name: edges[name] & produced for name in edges}
    changed = True
    while changed:
        changed = False
        for name, refs in edges.items():
            additions: set[str] = set()
            for ref in refs:
                additions |= closure.get(ref, set())
            if not additions <= closure[name]:
                closure[name] |= additions
                changed = True
    return closure


def _play_fact_reads(text: str, closure: dict[str, set[str]]) -> dict[str, str]:
    """Play-produced facts the task text reads unguardedly — directly would
    mean naming the fact; in practice through a role default — mapped to the
    default that mediated each read."""
    reads: dict[str, str] = {}
    for default_name, reachable in closure.items():
        if not reachable or not _unguarded_reads(text, default_name):
            continue
        for fact in reachable:
            reads.setdefault(fact, default_name)
    return reads


def test_tasks_reading_play_level_facts_run_under_their_loader_tags() -> None:
    producers = _play_producers()
    closure = _default_reference_closure(role_defaults(ROLE), set(producers))
    failures = []
    for task, effective, source in _walked():
        text = _reads_text(task)
        for fact, mediated_by in sorted(_play_fact_reads(text, closure).items()):
            loader_tags, loader_source = producers[fact]
            for tag in COMPONENT_TAGS:
                if _runs_under(effective, tag) and not _runs_under(loader_tags, tag):
                    failures.append(
                        f"{source}: {task.get('name', '?')!r} reads {fact}"
                        + (f" (through default {mediated_by})" if mediated_by else "")
                        + f" under {tag}, but its play-level producer "
                        f"({loader_source}) runs only under "
                        f"{[t for t in loader_tags if t != 'always']}"
                    )
                    break
    assert not failures, "\n".join(failures)


def test_component_tags_set_the_facts_their_tasks_read() -> None:
    defaults = role_defaults(ROLE)
    tree = _walked()
    producers: dict[str, tuple[dict, tuple[str, ...], str]] = {}
    for task, effective, source in tree:
        for name in _fact_names(task, defaults):
            producers.setdefault(name, (task, effective, source))

    failures = []
    # Every task in the tree (the producer itself excluded — reading your own
    # register is not a gap) that reads a fact under a component tag the task
    # setting the fact does not carry.
    for task, effective, source in tree:
        text = _reads_text(task)
        for name in sorted(producers):
            producer, producer_tags, producer_source = producers[name]
            if producer is task or not _unguarded_reads(text, name):
                continue
            for tag in COMPONENT_TAGS:
                if _runs_under(effective, tag) and not _runs_under(producer_tags, tag):
                    failures.append(
                        f"{source}: {task.get('name', '?')!r} reads {name} under "
                        f"{tag}, but its producer {producer.get('name', '?')!r} "
                        f"({producer_source}) runs only under "
                        f"{[t for t in producer_tags if t != 'always']}"
                    )
                    break
    assert not failures, "\n".join(failures)
