"""Opt-in live proof for tracker bot identity by caller key."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "hermes_agent"))
from _shell_harness_shared import defaults_of  # noqa: E402

ROUTER = defaults_of("llm_router")
TASKS = REPO_ROOT / "roles/llm_router/tasks/verify-tracker-agent-callers.yml"
SEED_TASKS = REPO_ROOT / "roles/llm_router/tasks/seed-consumers.yml"


def _tasks(path: Path) -> list[dict]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, list)
    return value


def test_switch_defaults_off() -> None:
    assert ROUTER["llm_router_tracker_agent_caller_check_enabled"] is False


def test_include_is_opt_in_and_skips_check_mode() -> None:
    include = next(
        task
        for task in _tasks(SEED_TASKS)
        if task.get("ansible.builtin.import_tasks")
        == "verify-tracker-agent-callers.yml"
    )
    tags = include.get("tags", [])
    when = include.get("when", [])
    if isinstance(tags, str):
        tags = [tags]
    if isinstance(when, str):
        when = [when]

    assert "llm_router_tracker_agent_callers" in tags
    conditions = " ".join(when)
    assert "llm_router_tracker_agent_caller_check_enabled" in conditions
    assert "llm_router_hermes_vikunja_clerk_variants_enabled" in conditions
    assert "not ansible_check_mode" in conditions


def test_sensitive_tasks_are_hidden_and_result_debug_is_visible() -> None:
    tasks = _tasks(TASKS)
    uri_tasks = [task for task in tasks if "ansible.builtin.uri" in task]
    fact_tasks = [task for task in tasks if "ansible.builtin.set_fact" in task]
    debug_tasks = [task for task in tasks if "ansible.builtin.debug" in task]

    assert uri_tasks
    assert fact_tasks
    assert all(task.get("no_log") is True for task in uri_tasks + fact_tasks)
    assert len(debug_tasks) == 1
    assert debug_tasks[0].get("no_log", False) is False


def test_result_debug_has_exactly_the_public_safe_fields() -> None:
    debug = next(task for task in _tasks(TASKS) if "ansible.builtin.debug" in task)
    assert set(debug["ansible.builtin.debug"]["msg"]) == {
        "caller",
        "model",
        "status",
        "task_id",
        "creator",
        "project_id",
    }


def test_rows_follow_the_caller_role_policy() -> None:
    rows = ROUTER["llm_router_tracker_agent_caller_rows"]
    key_roles = ROUTER["_llm_router_vikunja_clerk_key_roles"]
    roles = ROUTER["_llm_router_vikunja_clerk_roles"]

    assert all(row["expect_role"] in roles for row in rows if row["expect_status"] == 200)
    for caller, role in key_roles.items():
        assert any(
            row["caller"] == caller
            and row["expect_status"] == 200
            and row["expect_role"] == role
            for row in rows
        )


def test_chat_request_has_no_retry_or_output_token_limit() -> None:
    post = next(
        task["ansible.builtin.uri"]
        for task in _tasks(TASKS)
        if "ansible.builtin.uri" in task
        and task["ansible.builtin.uri"].get("method") == "POST"
    )
    assert post.get("retries", 0) == 0
    assert "max_tokens" not in post.get("body", {})
