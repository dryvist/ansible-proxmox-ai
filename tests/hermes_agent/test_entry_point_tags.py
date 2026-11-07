"""The Hermes entry-tag map covers the role task files without copying work."""

from __future__ import annotations

from pathlib import Path

import yaml

from _role_files import include_file

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"
DEFAULTS = ROLE / "defaults" / "main" / "196-entry-point-tags.yml"
TAG_DATA_KEY = "hermes_agent_entry_point_task_tags"
PROBE_TAG = "hermes_delivery_probe"


def _tag_expression(filename: str) -> str:
    return "{{ hermes_agent_entry_point_task_tags['" + filename + "'] }}"


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


def test_main_task_files_have_one_declared_entry_tag_and_probe_stays_scoped() -> None:
    tag_data = yaml.safe_load(DEFAULTS.read_text())[TAG_DATA_KEY]
    main = yaml.safe_load((TASKS / "main.yml").read_text()) or []
    direct_calls = [
        (task, target)
        for task in _task_items(main)
        for key in ("ansible.builtin.import_tasks", "ansible.builtin.include_tasks")
        if isinstance((target := include_file(task, key)), str) and target.endswith(".yml")
    ]

    assert direct_calls
    for task, target in direct_calls:
        assert target in tag_data
        tags = task.get("tags", [])
        tags = [tags] if isinstance(tags, str) else tags
        entry_tag_refs = [
            tag for tag in tags
            if isinstance(tag, str) and tag.startswith("{{ hermes_agent_entry_point_task_tags[")
        ]
        expected_refs = {_tag_expression(target)}
        if target == "prompt_catalog.yml":
            assert "always" in tags
            expected_refs.add(_tag_expression("cron_reconcile.yml"))
        if target == "bundle_build.yml":
            expected_refs.add(_tag_expression("public_gateway.yml"))
        assert set(entry_tag_refs) == expected_refs

    reachable: set[str] = set()
    pending = [target for _, target in direct_calls]
    while pending:
        filename = pending.pop()
        if filename in reachable:
            continue
        reachable.add(filename)
        for task, target in _entry_calls(filename):
            assert target in tag_data
            pending.append(target)
            tags = task.get("tags", [])
            tags = [tags] if isinstance(tags, str) else tags
            assert _tag_expression(target) in tags

    file_entries = {key for key in tag_data if key.endswith(".yml")}
    assert reachable == file_entries
    assert tag_data["delivery_probe.yml"] == PROBE_TAG
    probe_keys = {"delivery_probe.yml", "@probe_end_host"}
    assert {key for key, tag in tag_data.items() if tag == PROBE_TAG} == probe_keys

    probe_refs = set()
    for task in _task_items(main):
        tags = task.get("tags", [])
        tags = [tags] if isinstance(tags, str) else tags
        assert PROBE_TAG not in tags
        for tag in tags:
            prefix = "{{ hermes_agent_entry_point_task_tags['"
            suffix = "'] }}"
            if isinstance(tag, str) and tag.startswith(prefix) and tag.endswith(suffix):
                key = tag[len(prefix):-len(suffix)]
                if tag_data.get(key) == PROBE_TAG:
                    probe_refs.add(key)
    assert probe_refs == probe_keys
