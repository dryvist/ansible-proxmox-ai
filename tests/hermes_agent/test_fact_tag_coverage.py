"""Fail when a component tag reaches a task that reads a fact its producer
does not set under that same tag.

The component-tag narrowing (test_component_tag_narrowing.py) splits the
converge into per-component task selections. A fact set in one component but
read in another makes the reading component's scoped run fail on an
undefined variable — untagged runs never notice, because there every task
runs. This walks the same static tree the selection resolves (imports in
place, dynamic includes and foreign roles opaque, block/rescue/always
flattened) and requires every task reading a role-set fact to run only
under component tags the task setting the fact also carries. References a
reader guards itself — `| default(...)` and `is defined` — do not require
coverage: they stay defined by design in a scoped run.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from _role_files import include_file, role_defaults, template_text

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"
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
    "hermes_delivery_probe",
)

# A reader that pipes the fact into default(...) or tests it with
# `is (not) defined` stays defined in a scoped run on its own.
_DEFAULT_GUARD = re.compile(r"\s*(?:\.\w+)*\s*\|\s*default\(")
_DEFINED_GUARD = re.compile(r"\s+is\s+(?:not\s+)?defined\b")


def _task_tags(task: dict) -> list[str]:
    tags = task.get("tags", [])
    return [tags] if isinstance(tags, str) else list(tags or [])


def _walk(tasks: list[dict], inherited: tuple[str, ...],
          source: str) -> list[tuple[dict, tuple[str, ...], str]]:
    """Every task in the static tree as (task, effective tags, source file),
    in execution order — the same selection rules the narrowing test models."""
    walked: list[tuple[dict, tuple[str, ...], str]] = []
    for task in tasks:
        effective = tuple(sorted(set(inherited) | set(_task_tags(task))))
        static_target = include_file(task, "ansible.builtin.import_tasks")
        if isinstance(static_target, str) and static_target.endswith(".yml"):
            walked.extend(_walk(yaml.safe_load((TASKS / static_target).read_text()) or [],
                                effective, static_target))
            continue
        imported_role = task.get("ansible.builtin.import_role")
        if isinstance(imported_role, dict):
            if imported_role.get("name") == "hermes_agent":
                tasks_from = (imported_role.get("tasks_from") or "main") + ".yml"
                walked.extend(_walk(yaml.safe_load((TASKS / tasks_from).read_text()) or [],
                                    effective, tasks_from))
            continue
        if include_file(task, "ansible.builtin.include_tasks") is not None or task.get(
            "ansible.builtin.include_role"
        ):
            # Dynamic contents list as one opaque entry; their inner tasks
            # carry the include's own tags and are not attributable here.
            continue
        nested = [
            task.get(key) for key in ("block", "rescue", "always")
        ]
        nested = [children for children in nested if isinstance(children, list)]
        if nested:
            for children in nested:
                walked.extend(_walk(children, effective, source))
            continue
        walked.append((task, effective, source))
    return walked


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


def test_component_tags_set_the_facts_their_tasks_read() -> None:
    defaults = role_defaults(ROLE)
    tree = _walk(yaml.safe_load((TASKS / "main.yml").read_text()) or [], (), "main.yml")
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
