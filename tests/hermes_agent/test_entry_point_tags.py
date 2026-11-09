"""The Hermes entry-tag wiring is static and covers every task file once.

Tags on static imports are resolved before role defaults exist, so each
include in tasks/main.yml carries a literal component tag list (never a
value rendered from a variable) and `always` appears only on tasks that
guard every component. tests/hermes_agent/test_component_tag_narrowing.py
pins what each component tag then selects.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from _role_files import include_file

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"
COMPONENT_TAGS = {
    "hermes_runtime_install",
    "hermes_delivery_probe",
    "hermes_content_scripts",
    "hermes_patches",
    "hermes_profiles_env",
    "hermes_crons",
    "hermes_units_gateways",
}
PROBE_TAG = "hermes_delivery_probe"
# Reached only through agent-egress.yml's include_role (tasks_from: egress),
# so they are not part of main.yml's import tree.
EGRESS_FILES = {"egress.yml", "egress_domains.yml", "egress_host.yml"}


def _as_tag_list(task: dict) -> list[str]:
    tags = task.get("tags", [])
    return [tags] if isinstance(tags, str) else list(tags or [])


def _task_items(tasks: list[dict]) -> list[dict]:
    for task in tasks:
        yield task
        for child_key in ("block", "rescue", "always"):
            children = task.get(child_key, [])
            if isinstance(children, list):
                yield from _task_items(children)


def _entry_calls(filename: str) -> list[tuple[dict, str]]:
    tasks = yaml.safe_load((TASKS / filename).read_text()) or []
    calls = []
    for task in _task_items(tasks):
        for key in ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks"):
            target = include_file(task, key)
            if isinstance(target, str) and target.endswith(".yml"):
                calls.append((task, target))
    return calls


def test_main_task_files_have_static_tags_and_one_owner_tag_each() -> None:
    main = yaml.safe_load((TASKS / "main.yml").read_text()) or []
    direct_calls = [
        (task, target)
        for task in _task_items(main)
        for key in ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks")
        if isinstance((target := include_file(task, key)), str) and target.endswith(".yml")
    ]

    assert direct_calls
    owner_tags: dict[str, set[str]] = {}
    for task, target in direct_calls:
        tags = set(_as_tag_list(task))
        # Tags are literal: a variable-rendered tag cannot gate a static
        # import (ansible resolves it before role defaults exist).
        assert not any("{{" in tag for tag in tags), task["name"]
        component = tags & COMPONENT_TAGS
        if target == "pepper_profiles.yml":
            # Identity guard: unconditional, guarded in-line by the pepper
            # identity condition instead of by a component tag.
            assert tags == {"always"}
            continue
        assert component, f"{target} carries no component tag"
        assert component <= COMPONENT_TAGS
        owner_tags.setdefault(target, set()).update(component)
    # One entry point per file — a file is reached through a single call site
    # in main.yml, whatever list of components consumes it.
    call_sites = [target for _, target in direct_calls]
    assert len(call_sites) == len(set(call_sites))

    # prompt_catalog feeds the cron fleet, the content scripts, and the
    # public-gateway webhook config consumed under the units component.
    assert owner_tags["prompt_catalog.yml"] == {
        "hermes_content_scripts",
        "hermes_crons",
        "hermes_units_gateways",
    }
    # The content bundle serves the profiles component and the public gateway.
    assert owner_tags["bundle_build.yml"] == {"hermes_profiles_env", "hermes_units_gateways"}
    # The probe stays scoped to its own read-only component.
    assert owner_tags["delivery_probe.yml"] == {PROBE_TAG}
    for task in _task_items(main):
        if any(
            include_file(task, key) == "delivery_probe.yml"
            for key in ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks")
        ) or task.get("ansible.builtin.meta"):
            assert set(_as_tag_list(task)) == {PROBE_TAG}

    reachable: set[str] = set()
    pending = [target for _, target in direct_calls]
    while pending:
        filename = pending.pop()
        if filename in reachable:
            continue
        reachable.add(filename)
        owner = owner_tags.get(filename)
        for task, target in _entry_calls(filename):
            pending.append(target)
            tags = set(_as_tag_list(task))
            assert not any("{{" in tag for tag in tags), task["name"]
            if owner is not None:
                # Sub-includes stay inside their file's owner component.
                assert tags & COMPONENT_TAGS <= owner, (filename, target)

    every_file = {p.name for p in TASKS.glob("*.yml")} - {"main.yml"}
    assert reachable == every_file - EGRESS_FILES
