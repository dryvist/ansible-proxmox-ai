from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

from _role_files import role_defaults


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
TASK_ROOT = ROLE_ROOT / "tasks"


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text()) or []


def _guard_tasks() -> list[dict]:
    return [
        task
        for name in (
            "source_guard_check.yml",
            "source_guard_check_file.yml",
            "source_guard_record.yml",
        )
        for task in _tasks(TASK_ROOT / name)
    ]


def test_switch_defaults_off_and_every_guard_task_is_conditional() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_source_guard_enabled"] is False
    assert defaults["hermes_agent_source_guard_exclude"] == []
    for task in _guard_tasks():
        assert "hermes_agent_source_guard_enabled" in str(task.get("when", "")), task["name"]


def test_check_precedes_managed_writers_and_record_follows_the_last_one() -> None:
    config_tasks = _tasks(TASK_ROOT / "config_and_content.yml")
    check_index = next(
        index for index, task in enumerate(config_tasks)
        if task.get("ansible.builtin.include_tasks") == "source_guard_check.yml"
    )
    soul_fetch_index = next(
        index for index, task in enumerate(config_tasks)
        if task.get("ansible.builtin.import_tasks") == "fetch_profile_souls.yml"
    )
    managed_writers = [
        index for index, task in enumerate(config_tasks)
        if any(key in task for key in ("ansible.builtin.template", "ansible.builtin.copy"))
        and any(
            suffix in str(task.get(module, {}).get("dest", ""))
            for module in ("ansible.builtin.template", "ansible.builtin.copy")
            for suffix in ("config.yaml", "SOUL.md", "hindsight/config.json")
        )
    ]
    assert soul_fetch_index < check_index < min(managed_writers)

    main = (TASK_ROOT / "main.yml").read_text()
    assert main.index("import_tasks: config_and_content.yml") < main.index("import_tasks: ssh_and_cron_drift.yml")
    assert main.index("import_tasks: config_and_content.yml") < main.index("import_tasks: cron_reconcile.yml")

    profile_tasks = _tasks(TASK_ROOT / "skills_and_profiles.yml")
    profile_index = next(
        index for index, task in enumerate(profile_tasks)
        if task.get("name") == "Reconcile named Hermes operating profiles"
    )
    record_index = next(
        index for index, task in enumerate(profile_tasks)
        if task.get("ansible.builtin.include_tasks") == "source_guard_record.yml"
    )
    assert profile_index < record_index


def test_failure_message_names_files_and_says_to_put_change_in_git() -> None:
    tasks = _tasks(TASK_ROOT / "source_guard_check.yml")
    failure = next(task["ansible.builtin.fail"] for task in tasks if "ansible.builtin.fail" in task)
    assert "path" in failure["msg"]
    assert "put the change in git" in failure["msg"]


def test_file_list_uses_profiles_variable_and_excludes_suffixes() -> None:
    tasks = _tasks(TASK_ROOT / "source_guard_check.yml")
    profile_task = next(task for task in tasks if task.get("loop") == "{{ hermes_agent_profiles }}")
    expression = profile_task["ansible.builtin.set_fact"]["_hermes_agent_source_guard_files"]
    assert "item.name" in expression and "'/profiles/'" in expression

    environment = NativeEnvironment()
    environment.filters["bool"] = bool
    added = environment.from_string(expression).render(
        _hermes_agent_source_guard_files=[],
        hermes_agent_hermes_home="/home/hermes",
        hermes_agent_soul_managed=False,
        hermes_agent_memory_provider="builtin",
        item={"name": "added-in-test", "memory_enabled": False},
    )
    assert added[0]["path"] == "/home/hermes/profiles/added-in-test/config.yaml"

    exclusion_task = next(
        task for task in tasks
        if task.get("name") == "Apply the managed file suffix exclusions"
    )
    filtered = exclusion_task["ansible.builtin.set_fact"]["hermes_agent_source_guard_files"]
    assert "hermes_agent_source_guard_exclude" in filtered and ")$" in filtered
    environment.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
    environment.filters["regex_escape"] = re.escape
    excluded = environment.from_string(filtered).render(
        _hermes_agent_source_guard_files=[
            {"path": "/home/hermes/profiles/added-in-test/config.yaml"},
            {"path": "/home/hermes/profiles/added-in-test/SOUL.md"},
        ],
        hermes_agent_source_guard_exclude=["SOUL.md"],
    )
    assert [file["path"] for file in excluded] == [
        "/home/hermes/profiles/added-in-test/config.yaml"
    ]


def test_file_drift_is_two_sided() -> None:
    task = next(
        task for task in _tasks(TASK_ROOT / "source_guard_check_file.yml")
        if "_hermes_agent_source_guard_drifted" in task.get("ansible.builtin.set_fact", {})
    )
    expression = task["ansible.builtin.set_fact"]["_hermes_agent_source_guard_drifted"]
    environment = NativeEnvironment()

    def drift(recorded: str | None, current: str | None, desired: str) -> bool:
        context = {
            "_hermes_agent_source_guard_previous": {} if recorded is None else {"sha256": recorded},
            "_hermes_agent_source_guard_current": {
                "stat": {"exists": current is not None, "checksum": current}
            },
            "_hermes_agent_source_guard_expected_sha256": desired,
        }
        return environment.from_string(expression).render(**context)

    assert drift("same", "same", "same") is False
    assert drift("recorded", "hand-edit", "git-content") is True
    assert drift("recorded", "git-content", "git-content") is False
    assert drift(None, "first-run", "git-content") is False
    assert drift("recorded", None, "recorded") is False
    assert drift("recorded", None, "new-content") is True


def test_record_hides_bodies_and_uses_root_mode_0600() -> None:
    tasks = _guard_tasks()
    for task in tasks:
        assert "ansible.builtin.debug" not in task
        task_text = str(task)
        if "lookup('ansible.builtin.template'" in task_text or "lookup('ansible.builtin.file'" in task_text:
            assert task.get("no_log") is True, task["name"]
    record = next(
        task["ansible.builtin.copy"] for task in _tasks(TASK_ROOT / "source_guard_record.yml")
        if "ansible.builtin.copy" in task
    )
    assert record["owner"] == record["group"] == "root"
    assert record["mode"] == "0600"
    assert "sha256" in str(_tasks(TASK_ROOT / "source_guard_record.yml"))


def test_guard_writes_nothing_in_check_mode() -> None:
    copy_task = next(
        task for task in _tasks(TASK_ROOT / "source_guard_record.yml")
        if "ansible.builtin.copy" in task
    )
    assert "not ansible_check_mode" in str(copy_task["when"])
    assert not any(
        module in task
        for task in _tasks(TASK_ROOT / "source_guard_check.yml")
        for module in ("ansible.builtin.copy", "ansible.builtin.template", "ansible.builtin.file")
    )
