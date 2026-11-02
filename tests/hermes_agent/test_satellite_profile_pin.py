"""Hermes CLI profile pins and guarded satellite-cron migration contracts."""

from __future__ import annotations

import ast
import json
import re
import shlex
from pathlib import Path

import yaml
from jinja2 import Environment

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
ROOT = "/srv/hermes-home"
PROFILE = f"{ROOT}/profiles/homelab-admin"
BIN = "/usr/bin/hermes"
JINJA = Environment()


def _render(value, context: dict):
    if not isinstance(value, str):
        return value
    return JINJA.from_string(value).render(**context)


def _tasks(path: Path):
    document = yaml.safe_load(path.read_text()) or []
    for task in document:
        if not isinstance(task, dict):
            continue
        yield task
        for section in ("block", "rescue", "always"):
            if section in task:
                yield from task[section]


def _home_context(task: dict, home: str) -> dict:
    expression = task.get("vars", {}).get("hermes_agent_cli_home")
    if expression and "item.hermes_home" in expression:
        item = {"hermes_home": home, "name": "fixture", "schedule": "daily"}
    elif expression and "item.item" in expression:
        item = {"item": home, "name": "fixture", "schedule": "daily"}
    elif expression and "{{ item }}" in expression:
        item = home
    else:
        item = {
            "hermes_home": home,
            "item": home,
            "name": "fixture",
            "supersedes": "fixture",
            "schedule": "daily",
            "skill": "",
            "script": "",
        }
    context = {
        "hermes_agent_bin": BIN,
        "hermes_agent_hermes_home": ROOT,
        "hermes_agent_cli_home": home,
        "item": item,
        "hermes_agent_kanban_unblock_ids": [],
        "hermes_agent_kanban_unblock_reason": "fixture",
        "hermes_agent_enqueuer_selector": "",
        "hermes_agent_enqueuer_name": "fixture",
        "hermes_agent_enqueuer_script": "fixture.py",
        "hermes_agent_enqueuer_deliver": "local",
        "hermes_agent_cron_failure_deliver": "",
        "hermes_agent_hermes_home": ROOT,
        "hermes_agent_direct_prompt": "fixture prompt",
        "hermes_agent_direct_model": "",
        "hermes_agent_direct_model_provider": "",
        "hermes_agent_direct_deliver": "local",
    }
    expression = task.get("vars", {}).get("hermes_agent_cli_home")
    if expression:
        context["hermes_agent_cli_home"] = _render(expression, context)
    context["hermes_agent_cli_profile_args"] = _profile_args(context["hermes_agent_cli_home"])
    return context


def _profile_args(home: str) -> list[str]:
    defaults = yaml.safe_load((ROLE / "defaults/main/32-cli-profile-pin.yml").read_text())
    rendered = _render(defaults["hermes_agent_cli_profile_args"], {
        "hermes_agent_cli_home": home,
        "hermes_agent_hermes_home": ROOT,
    })
    return yaml.safe_load(rendered)


def _action_argv(action, context: dict) -> list[str]:
    if isinstance(action, dict):
        if "argv" in action:
            value = action["argv"]
            if isinstance(value, list):
                return [_render(part, context) for part in value]
            rendered = _render(value, context)
            if rendered.startswith("["):
                return ast.literal_eval(rendered)
            return shlex.split(rendered)
        action = action.get("cmd", "")
    return shlex.split(_render(action, context))


def _pinned_args_are_immediate(action) -> bool:
    text = json.dumps(action)
    string_form = re.search(
        r"\{\{\s*hermes_agent_bin\s*\}\}\s+"
        r"\{\{\s*hermes_agent_cli_profile_args\s*\|\s*join\(' '\)\s*\}\}",
        text,
    )
    argv_form = re.search(
        r"\[\s*hermes_agent_bin\s*\]\s*\+\s*hermes_agent_cli_profile_args", text
    )
    return bool(string_form or argv_form)


def test_every_hermes_binary_task_pins_root_and_leaves_profile_homes_unpinned() -> None:
    calls = []
    for path in sorted((ROLE / "tasks").rglob("*.yml")):
        for task in _tasks(path):
            action_key = next(
                (key for key in ("ansible.builtin.command", "ansible.builtin.shell") if key in task),
                None,
            )
            if action_key and "hermes_agent_bin" in json.dumps(task[action_key]):
                calls.append((path, task, task[action_key]))
    assert calls

    direct_create_builder = yaml.safe_load(
        (ROLE / "tasks/reconcile_direct_cron.yml").read_text()
    )
    builder = next(task for task in direct_create_builder if task["name"].startswith("Direct cron | Build create argv"))

    for path, task, action in calls:
        environment = task.get("environment", {})
        assert "HERMES_HOME" in environment, task["name"]
        dynamic = "hermes_agent_cli_home" in task.get("vars", {})
        if dynamic:
            homes = (ROOT, PROFILE)
        else:
            static_context = _home_context(task, ROOT)
            static_home = _render(environment["HERMES_HOME"], static_context)
            homes = (static_home,)
        if task["name"].startswith("Direct cron | Create "):
            assert action.get("argv") == "{{ hermes_agent_direct_argv }}"
            assert "[hermes_agent_bin] + hermes_agent_cli_profile_args" in str(
                builder["ansible.builtin.set_fact"]["hermes_agent_direct_argv"]
            )

        for home in homes:
            context = _home_context(task, home)
            selected_home = _render(environment["HERMES_HOME"], context)
            assert selected_home == home, (path.name, task["name"], selected_home)
            if task["name"].startswith("Direct cron | Create "):
                assert dynamic
                builder_context = dict(context)
                rendered = _render(
                    builder["ansible.builtin.set_fact"]["hermes_agent_direct_argv"],
                    builder_context,
                )
                context["hermes_agent_direct_argv"] = ast.literal_eval(rendered)
            elif dynamic or home == ROOT:
                assert _pinned_args_are_immediate(action), (path.name, task["name"])
            argv = _action_argv(action, context)
            assert argv[0] == BIN, (path.name, task["name"], argv)
            if home == ROOT:
                assert argv[1:3] == ["--profile", "default"], (path.name, task["name"], argv)
            else:
                assert "--profile" not in argv, (path.name, task["name"], argv)


def test_migration_count_exact_id_guards_and_check_mode_gate() -> None:
    defaults = yaml.safe_load((ROLE / "defaults/main/32-satellite-stray-migration.yml").read_text())
    assert defaults["hermes_agent_satellite_stray_migration_enabled"] is True
    assert defaults["hermes_agent_satellite_stray_expected_count"] == 10
    assert len(defaults["hermes_agent_satellite_stray_jobs"]) == 10

    migration = yaml.safe_load((ROLE / "tasks/cron_satellite_stray_migration.yml").read_text())
    assert migration[0]["ansible.builtin.assert"]["that"] == [
        "hermes_agent_satellite_stray_jobs | length == hermes_agent_satellite_stray_expected_count"
    ]
    item_tasks = yaml.safe_load((ROLE / "tasks/cron_satellite_stray_migration_item.yml").read_text())
    remove = next(task for task in item_tasks if task["name"].startswith("Satellite stray migration | Remove"))
    assert remove["ansible.builtin.command"]["argv"] == [
        "{{ hermes_agent_bin }}", "cron", "remove", "{{ item.id }}"
    ]
    assert remove["no_log"] is True
    assert set(remove["when"]) == {
        "hermes_agent_satellite_stray_current.get('id', '') == item.id",
        "hermes_agent_satellite_stray_current.get('name', '') == item.name",
        "item.name in hermes_agent_satellite_stray_primary_names",
    }

    cron_reconcile = yaml.safe_load((ROLE / "tasks/cron_reconcile.yml").read_text())
    include = next(
        task for task in cron_reconcile
        if task.get("ansible.builtin.include_tasks") == "cron_satellite_stray_migration.yml"
    )
    assert include["when"] == [
        "hermes_agent_satellite_stray_migration_enabled | bool",
        "not ansible_check_mode",
    ]
