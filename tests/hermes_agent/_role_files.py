"""Read a role's split defaults/tasks as one logical unit.

Several roles in this repo keep their `defaults/main.yml` and
`tasks/main.yml` under the repo's per-file token budget by splitting them
into `defaults/main/<topic>.yml` directories and thin `tasks/main.yml` +
`include_tasks` files (see .token-limits.yaml). Tests that pin values or
strings from "the role's defaults" or "the role's tasks" need the SAME
content Ansible itself loads, not one arbitrary fragment of it — these
helpers concatenate/merge the split files back into what a single-file role
would have provided, so every existing assertion keeps meaning what it did
before the split.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

# Matches a whole-value Jinja list-concatenation, e.g.
#   hermes_agent_direct_cron_jobs: "{{ hermes_agent_direct_cron_jobs_assistant
#     + hermes_agent_direct_cron_jobs_legacy_v2 + hermes_agent_direct_cron_jobs_core }}"
# — the pattern one oversized list-valued default is split into (per-part
# files, concatenated back under the original name) when the individual
# entries can't be thinned any further. yaml.safe_load leaves this as a
# literal Jinja string; Ansible itself renders it at converge time the same
# way this resolves it here.
_CONCAT_RE = re.compile(r"^\{\{\s*([\w\s+]+?)\s*\}\}$")


def role_defaults(role_root: Path) -> dict:
    """Merge every file in defaults/main/ into one dict, in filename order,
    then resolve any whole-value `{{ a + b + c }}` list concatenation.
    """
    merged: dict = {}
    for f in sorted((role_root / "defaults" / "main").glob("*.yml")):
        merged.update(yaml.safe_load(f.read_text()) or {})
    for key, value in list(merged.items()):
        if not isinstance(value, str):
            continue
        match = _CONCAT_RE.match(value)
        if not match:
            continue
        parts = [p.strip() for p in match.group(1).split("+")]
        if all(p in merged and isinstance(merged[p], list) for p in parts):
            resolved: list = []
            for p in parts:
                resolved.extend(merged[p])
            merged[key] = resolved
    return merged


def role_defaults_text(role_root: Path) -> str:
    """Concatenate defaults/main/*.yml for substring/regex assertions."""
    return "\n".join(
        f.read_text() for f in sorted((role_root / "defaults" / "main").glob("*.yml"))
    )


_INCLUDE_KEYS = ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks")


def role_tasks(role_root: Path, entry: str = "main.yml") -> list:
    """Resolve tasks/<entry> into the flat task list Ansible would actually
    run, in REAL execution order — not alphabetical file order.

    A thin file's include_tasks/import_tasks entries are expanded in place
    (recursively, since a split file like assert.yml is itself a thin
    wrapper over further files); a real task is kept as-is. This is what
    yaml.safe_load(tasks/main.yml) returned before the split, order included
    — tests that assert "task A runs before task B" depend on that order.
    """
    raw = yaml.safe_load((role_root / "tasks" / entry).read_text()) or []
    flat: list = []
    for item in raw:
        target = None
        for key in _INCLUDE_KEYS:
            if key in item and isinstance(item[key], str) and item[key].endswith(".yml"):
                target = item[key]
                break
        if target:
            flat.extend(role_tasks(role_root, target))
        else:
            flat.append(item)
    return flat


def _split_top_level_items(text: str) -> list[str]:
    """Split a tasks-file's raw text into one chunk per top-level `- name:`
    list item (plus its own trailing comments/blank lines), preserving exact
    source formatting. Header comments before the first item are dropped —
    only per-task text is needed here.
    """
    items: list[str] = []
    current: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("- "):
            if current:
                items.append("".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        items.append("".join(current))
    return items


def role_tasks_text(role_root: Path, entry: str = "main.yml") -> str:
    """Concatenate the UNMODIFIED text of every task in main.yml's
    include/import chain, in REAL execution order — an include/import item
    is replaced by its target file's own resolved text; a real task keeps
    its exact source text. Preserves exact source formatting (quoting,
    indentation, a `block: |` scalar's original layout) — a reformatting
    round-trip (e.g. yaml.dump) would silently change quote style and break
    substring/regex assertions written against the source text.
    """
    file_text = (role_root / "tasks" / entry).read_text()
    raw = yaml.safe_load(file_text) or []
    chunks = _split_top_level_items(file_text)
    assert len(chunks) == len(raw), (
        f"{entry}: parsed {len(raw)} top-level tasks but split {len(chunks)} "
        "raw text chunks — a task's YAML must start its `- ` at column 0"
    )
    parts = []
    for item, chunk in zip(raw, chunks):
        target = None
        for key in _INCLUDE_KEYS:
            if key in item and isinstance(item[key], str) and item[key].endswith(".yml"):
                target = item[key]
                break
        # Keep the include/import task's OWN text (its name is a position
        # marker some tests search for — "task A runs before task B") AND
        # splice in what it pulls in, so content-level checks still find
        # whatever moved into the target file.
        parts.append(chunk)
        if target:
            parts.append(role_tasks_text(role_root, target))
    return "\n".join(parts)
