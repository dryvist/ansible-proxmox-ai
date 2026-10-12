"""The router database preflight (playbooks/llm-router-db-preflight.yml and
roles/llm_router/tasks/db-preflight.yml) must stay read-only and must never run
in a converge.

Parsed with yaml.safe_load, never a string grep, so each assertion is about the
structure Ansible actually executes: the play's tags, each task's keywords, and
the SQL strings that psql receives.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = REPO_ROOT / "playbooks/llm-router-db-preflight.yml"
SITE = REPO_ROOT / "playbooks/site.yml"
TASK_FILE = REPO_ROOT / "roles/llm_router/tasks/db-preflight.yml"
WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|GRANT|REVOKE)\b", re.IGNORECASE
)


def _load(path: Path) -> list:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _preflight_play() -> dict:
    plays = [p for p in _load(PLAYBOOK) if "llm_router_db_preflight" in (p.get("tags") or [])]
    assert len(plays) == 1, "expected exactly one play tagged llm_router_db_preflight"
    return plays[0]


def _tasks() -> list[dict]:
    return _load(TASK_FILE)


def _is_command(task: dict) -> bool:
    return "ansible.builtin.command" in task


def test_files_exist():
    assert PLAYBOOK.is_file(), PLAYBOOK
    assert TASK_FILE.is_file(), TASK_FILE


def test_play_is_tagged_never_and_selected_only_by_its_own_tag():
    play = _preflight_play()
    assert "never" in play["tags"], "a converge must never run the preflight"
    assert "llm_router_db_preflight" in play["tags"]


def test_play_targets_the_router_group():
    assert _preflight_play()["hosts"] == "llm_router_group"


def test_site_imports_the_playbook():
    imports = [p.get("import_playbook") for p in _load(SITE)]
    assert "llm-router-db-preflight.yml" in imports


def test_every_task_that_runs_sql_is_changed_when_false_and_run_once():
    command_tasks = [t for t in _tasks() if _is_command(t)]
    assert command_tasks, "expected psql tasks in the preflight task file"
    for task in command_tasks:
        assert task.get("changed_when") is False, task.get("name")
        assert task.get("run_once") is True, task.get("name")


def test_every_task_that_carries_the_password_is_no_log():
    for task in _tasks():
        env = task.get("environment") or {}
        if "PGPASSWORD" in env:
            assert task.get("no_log") is True, task.get("name")


def test_password_never_reaches_argv():
    for task in _tasks():
        if _is_command(task):
            argv = task["ansible.builtin.command"]["argv"]
            assert not any("llm_router_db_password" in str(arg) for arg in argv), task.get("name")


def test_every_sql_statement_is_a_select_with_no_write_keyword():
    statements = []
    for task in _tasks():
        if _is_command(task):
            statements += [str(arg) for arg in task["ansible.builtin.command"]["argv"]
                           if "SELECT" in str(arg).upper()]
    assert statements, "expected at least one SQL statement"
    for statement in statements:
        assert statement.lstrip().upper().startswith("SELECT"), statement
        assert not WRITE_KEYWORDS.search(statement), statement


def test_every_psql_task_runs_read_only():
    for task in _tasks():
        if _is_command(task):
            env = task.get("environment") or {}
            assert "default_transaction_read_only=on" in env.get("PGOPTIONS", ""), task.get("name")
