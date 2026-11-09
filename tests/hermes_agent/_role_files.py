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


def _task_items(tasks: list[dict]):
    """Yield tasks nested under block, rescue, or always containers."""
    for task in tasks:
        yield task
        for child_key in ("block", "rescue", "always"):
            children = task.get(child_key, [])
            if isinstance(children, list):
                yield from _task_items(children)


def role_defaults(role_root: Path) -> dict:
    """Merge every file in defaults/main/ into one dict, in filename order,
    then resolve any whole-value `{{ a + b + c }}` list concatenation.
    """
    merged: dict = {}
    for f in sorted((role_root / "defaults" / "main").glob("*.yml")):
        merged.update(yaml.safe_load(f.read_text()) or {})
    if "hermes_agent_catalog_prompt_sources" in merged:
        from _prompt_catalog import prompt_bodies

        # Content contracts inspect the same literal bodies as before the move.
        # Real Ansible template expansion is checked by test_prompt_catalog_render.
        merged.update(prompt_bodies(merged))
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


def hermes_runtime_role_root() -> Path:
    """Return the Hermes runtime role installed from requirements.yml."""
    root = (
        Path.home()
        / ".ansible/collections/ansible_collections/dryvist/ai_agents/roles/hermes_runtime"
    )
    assert root.is_dir(), "install requirements.yml before testing Hermes runtime units"
    return root


def render_hermes_runtime_unit(consumer_role: Path, template_name: str, **overrides) -> str:
    """Render a collection unit with the consumer's actual include_role vars."""
    from ansible.plugins.filter.core import dict_to_list_of_dict_key_value_elements
    from jinja2 import StrictUndefined
    from jinja2.nativetypes import NativeEnvironment

    include_task = next(
        task
        for task in yaml.safe_load((consumer_role / "tasks" / "main.yml").read_text())
        if isinstance(task.get("ansible.builtin.import_role"), dict)
        and task["ansible.builtin.import_role"].get("name")
        == "dryvist.ai_agents.hermes_runtime"
        and task["ansible.builtin.import_role"].get("tasks_from") == "units"
    )
    context = role_defaults(consumer_role)
    context.update(hermes_agent_uid=1001, hermes_agent_dashboard_port=8080)
    context.update(overrides)
    env = NativeEnvironment(autoescape=False, undefined=StrictUndefined)
    env.filters['dict2items'] = dict_to_list_of_dict_key_value_elements
    for key in (
        "hermes_agent_hermes_home",
        "hermes_agent_web_dist",
        "hermes_agent_gateway_cmd",
    ):
        context[key] = env.from_string(context[key]).render(**context)

    def render_value(value):
        if isinstance(value, str):
            return env.from_string(value).render(**context) if "{{" in value else value
        if isinstance(value, list):
            return [render_value(item) for item in value]
        if isinstance(value, dict):
            return {key: render_value(item) for key, item in value.items()}
        return value

    runtime_vars = {key: render_value(value) for key, value in include_task["vars"].items()}
    runtime_vars["ansible_managed"] = "managed by test"
    env.filters["bool"] = bool
    env.filters["comment"] = lambda value, *args, **kwargs: "\n".join(
        f"# {line}" for line in str(value).splitlines()
    )
    template = (hermes_runtime_role_root() / "templates" / template_name).read_text()
    return env.from_string(template).render(**runtime_vars)


_JINJA_INCLUDE_RE = re.compile(r"^[ \t]*\{%-?\s*include\s+'([^']+)'\s*-?%\}[ \t]*$", re.MULTILINE)


def template_text(role_root: Path, name: str) -> str:
    """Read templates/<name> with every `{% include 'x' %}` spliced in.

    Same motive as role_tasks_text below: a template split into a shared
    partial (hermes-env.j2 + hermes-env-operational.j2) still renders as one
    file, so a test that pins a line of it has to see the whole thing. Reading
    the caller alone would silently stop covering whatever moved into the
    partial — passing for the wrong reason.
    """
    text = (role_root / "templates" / name).read_text()
    return _JINJA_INCLUDE_RE.sub(lambda m: template_text(role_root, m.group(1)), text)


_INCLUDE_KEYS = ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks")


def include_file(task: dict, key: str = "ansible.builtin.include_tasks") -> str | None:
    """Return an include/import target from either Ansible YAML form."""
    target = task.get(key)
    return target.get("file") if isinstance(target, dict) else target


def task_tags(task: dict) -> list[str]:
    """A task's declared tags, in either YAML scalar or list form."""
    tags = task.get("tags", [])
    return [tags] if isinstance(tags, str) else list(tags or [])


def walk_tagged_tasks(
    tasks: list[dict],
    base: Path,
    inherited: tuple[str, ...] = (),
    source: str = "main.yml",
    self_role: str = "hermes_agent",
    self_tasks_dir: Path | None = None,
) -> list[tuple[dict, tuple[str, ...], str, bool]]:
    """Resolve a task list the way ansible's tag selection lists it, as
    (task, effective tags, source file, opaque) in execution order — the
    one traversal the component tag tests share.

    Static import_tasks targets splice in place and inherit the import's
    tags; block/rescue/always children flatten; an import_role of
    `self_role` splices its tasks_from file in place. Everything that lists
    as ONE entry — a dynamic include_tasks/include_role and a foreign
    import_role — comes back with opaque=True: callers modeling
    --list-tasks keep it, callers attributing facts or behavior to
    individual tasks skip it. A foreign import_role (hermes_runtime,
    bao_approle_cli) is opaque because its contents resolve from the
    installed collection, so one entry inheriting the import tags is
    exactly how the selection treats it.
    """
    if self_tasks_dir is None:
        self_tasks_dir = base
    walked: list[tuple[dict, tuple[str, ...], str, bool]] = []
    for task in tasks:
        effective = tuple(sorted(set(inherited) | set(task_tags(task))))
        static_target = include_file(task, "ansible.builtin.import_tasks")
        if isinstance(static_target, str) and static_target.endswith(".yml"):
            walked.extend(walk_tagged_tasks(
                yaml.safe_load((base / static_target).read_text()) or [],
                base, effective, static_target, self_role, self_tasks_dir))
            continue
        imported_role = task.get("ansible.builtin.import_role")
        if imported_role is not None:
            if isinstance(imported_role, dict) and imported_role.get("name") == self_role:
                tasks_from = (imported_role.get("tasks_from") or "main") + ".yml"
                walked.extend(walk_tagged_tasks(
                    yaml.safe_load((self_tasks_dir / tasks_from).read_text()) or [],
                    self_tasks_dir, effective, tasks_from, self_role, self_tasks_dir))
                continue
            walked.append((task, effective, source, True))
            continue
        dynamic_target = include_file(task, "ansible.builtin.include_tasks")
        if isinstance(dynamic_target, str) and dynamic_target.endswith(".yml"):
            walked.append((task, effective, source, True))
            continue
        if task.get("ansible.builtin.include_role") is not None:
            walked.append((task, effective, source, True))
            continue
        nested = [task.get(key) for key in ("block", "rescue", "always")]
        nested = [children for children in nested if isinstance(children, list)]
        if nested:
            for children in nested:
                walked.extend(walk_tagged_tasks(
                    children, base, effective, source, self_role, self_tasks_dir))
            continue
        walked.append((task, effective, source, False))
    return walked


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
            include_target = include_file(item, key)
            if isinstance(include_target, str) and include_target.endswith(".yml"):
                target = include_target
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
            include_target = include_file(item, key)
            if isinstance(include_target, str) and include_target.endswith(".yml"):
                target = include_target
                break
        # Keep the include/import task's OWN text (its name is a position
        # marker some tests search for — "task A runs before task B") AND
        # splice in what it pulls in, so content-level checks still find
        # whatever moved into the target file.
        parts.append(chunk)
        if target:
            parts.append(role_tasks_text(role_root, target))
    return "\n".join(parts)
