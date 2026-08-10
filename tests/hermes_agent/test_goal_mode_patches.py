from __future__ import annotations

import re
from typing import Any

import pytest

from conftest import (
    ACTIVE_STATUSES,
    JUDGE_ERROR,
    ROLE_ROOT,
    _apply_runtime_patch,
    _goal_fields,
    _replace_task,
    _patched_create_task,
    _patched_goal_loop,
    _task,
    _task_db,
    PINNED_GOAL_COMPLETION_SOURCE,
    PINNED_HINDSIGHT_PREFETCH_SOURCE,
    PINNED_WORKER_SPAWN_SOURCE,
    PATCHED_KANBAN_GOAL_LOOP_SOURCE,
    role_tasks,
    role_tasks_text,
)

def test_goal_completion_patch_uses_current_judge_contract() -> None:
    patched = _apply_runtime_patch(
        "Patch Hermes goal completion gate for the four-value judge result",
        PINNED_GOAL_COMPLETION_SOURCE,
    )
    assert "verdict, reason, _, _ = judge_goal(" in patched


def test_hindsight_prefetch_patch_logs_at_warning() -> None:
    patched = _apply_runtime_patch(
        "Patch Hermes auto-recall prefetch failure to log at warning, not debug",
        PINNED_HINDSIGHT_PREFETCH_SOURCE,
    )
    assert 'logger.warning("Hindsight prefetch failed: %s", e, exc_info=True)' in patched
    assert "logger.debug" not in patched


def test_auxiliary_retry_budgets_are_bounded() -> None:
    """What remains true after the client-side backoff hacks were reverted.

    Retry *counts* (how many attempts) are a deliberate, unrelated config —
    unlike the removed patches, which forced a flat *wait time* between
    retries. Only the counts and the auxiliary-path fixed delay survive.
    """
    config_template = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()
    assert "Upstream counts total attempts" in config_template
    assert "api_max_retries: 2" in config_template
    assert "transient_retries: 1" in config_template

    tasks = role_tasks_text(ROLE_ROOT)
    assert "_TRANSIENT_RETRY_BACKOFF_BASE = 15.0" in tasks
    assert "status in (408, 429)" in tasks
    assert 'resolved_provider != "custom"' in tasks


# The six conversation_loop.py patches that forced a flat unjittered retry
# wait, disabled adaptive rate-limit backoff and transport recovery, and
# permanently forced debug logging on. All six reapplied on every converge
# once merged; removing them from the task list is the actual fix, so the
# regression this guards is a silent re-add of any one of them.
REVERTED_CLIENT_BACKOFF_PATCH_NAMES = (
    "Patch Hermes exception retry delay for the local serial backend",
    "Patch Hermes invalid-response retry delay for the local serial backend",
    "Disable adaptive rate-limit backoff for the local serial backend",
    "Disable the extra transport-recovery attempt cycle",
    "Enable prompt-safe Hermes request size metrics at DEBUG",
    "Enable prompt-safe Hermes token usage metrics at DEBUG",
)
KEPT_MAX_TOKENS_CEILING_PATCH_NAMES = (
    "Patch hermes-agent retry boost to respect the configured max_tokens ceiling",
    "Patch hermes-agent length-continuation boost to respect the configured "
    "max_tokens ceiling",
)


def test_client_side_backoff_hacks_stay_reverted() -> None:
    task_names = {
        item.get("name")
        for item in role_tasks(ROLE_ROOT)
    }
    for name in REVERTED_CLIENT_BACKOFF_PATCH_NAMES:
        assert name not in task_names, f"reintroduced: {name}"
    for name in KEPT_MAX_TOKENS_CEILING_PATCH_NAMES:
        assert name in task_names, f"missing: {name}"

    tasks_text = role_tasks_text(ROLE_ROOT)
    assert "wait_time = 15.0" not in tasks_text
    assert "if False and is_rate_limited" not in tasks_text
    assert "if False and not _retry.primary_recovery_attempted" not in tasks_text


def test_worker_spawn_patch_enters_quiet_goal_loop_path() -> None:
    patched = _apply_runtime_patch(
        "Patch Hermes Kanban workers to enter the quiet goal-loop path",
        PINNED_WORKER_SPAWN_SOURCE,
    )
    quiet_expansion = '        *(["--quiet"] if task.goal_mode else []),\n'
    assert patched.count(quiet_expansion) == 1
    assert patched.index('"chat"') < patched.index('["--quiet"]')
    assert patched.index('["--quiet"]') < patched.index('"-q", prompt')

    patched_again = _apply_runtime_patch(
        "Patch Hermes Kanban workers to enter the quiet goal-loop path",
        patched,
    )
    assert patched_again == patched

    duplicated = PINNED_WORKER_SPAWN_SOURCE.replace(
        '        "-q", prompt,\n',
        quiet_expansion * 17 + '        "-q", prompt,\n',
    )
    normalized = _apply_runtime_patch(
        "Patch Hermes Kanban workers to enter the quiet goal-loop path",
        duplicated,
    )
    assert normalized == patched

    namespace: dict[str, Any] = {}
    exec(patched, namespace)
    task_type = type("Task", (), {})
    goal_task = task_type()
    goal_task.goal_mode = True
    ordinary_task = task_type()
    ordinary_task.goal_mode = False
    assert namespace["build_worker_argv"](goal_task, "work") == [
        "chat",
        "--quiet",
        "-q",
        "work",
    ]
    assert namespace["build_worker_argv"](ordinary_task, "work") == [
        "chat",
        "-q",
        "work",
    ]


def test_judge_call_failure_consumes_no_turns_and_blocks_retryable() -> None:
    # The measured defect: 8/8 turns burned in minutes on nothing but judge
    # infrastructure errors, zero worker tool calls. A judge failure must
    # never consume budget, and a sustained outage must end in the sticky
    # blocked (retryable) state instead of spinning forever.
    loop, fake_time = _patched_goal_loop([JUDGE_ERROR] * 99)
    worker_prompts: list[str] = []
    blocks: list[str] = []

    result = loop(
        task_id="t",
        goal_text="g",
        run_turn=lambda prompt: worker_prompts.append(prompt) or "reply",
        task_status_fn=lambda: "running",
        block_fn=blocks.append,
        max_turns=8,
        first_response="started",
    )

    assert result["outcome"] == "blocked_judge_unreachable"
    assert result["turns_used"] == 1
    assert worker_prompts == []
    assert len(blocks) == 1
    assert "no worker turns were consumed" in blocks[0]
    # Five bounded attempts, the flat 15s local-backend delay between them.
    assert fake_time.sleeps == [15.0] * 4


def test_judge_failure_counter_resets_on_any_real_verdict() -> None:
    # Eight judge failures in total, but a real verdict between the two
    # bursts resets the counter — the loop never blocks, the worker keeps
    # its budget and finishes.
    loop, fake_time = _patched_goal_loop(
        [
            *[JUDGE_ERROR] * 4,
            ("continue", "made progress", False, None),
            *[JUDGE_ERROR] * 4,
        ]
    )
    state = {"worker_turns": 0}

    def run_turn(prompt: str) -> str:
        state["worker_turns"] += 1
        return "reply"

    blocks: list[str] = []
    result = loop(
        task_id="t",
        goal_text="g",
        run_turn=run_turn,
        task_status_fn=lambda: "done" if state["worker_turns"] >= 2 else "running",
        block_fn=blocks.append,
        max_turns=8,
        first_response="started",
    )

    assert result["outcome"] == "completed_by_worker"
    assert blocks == []
    assert state["worker_turns"] == 2
    assert result["turns_used"] == 3
    assert fake_time.sleeps == [15.0] * 8


def test_judge_error_guard_patches_are_idempotent() -> None:
    # The counter patch self-normalizes (optional-group regexp): re-applying
    # it to already-patched source is byte-identical.
    again = _apply_runtime_patch(
        "Patch Hermes kanban goal loop to count consecutive judge failures",
        PATCHED_KANBAN_GOAL_LOOP_SOURCE,
    )
    assert again == PATCHED_KANBAN_GOAL_LOOP_SOURCE
    # The guard patch rewrites its own anchor, so on patched source it must
    # find nothing — never a second insertion.
    config = _replace_task(
        "Patch Hermes kanban goal loop to retry judge errors without burning turns"
    )
    patched, count = re.subn(
        config["regexp"],
        config["replace"],
        PATCHED_KANBAN_GOAL_LOOP_SOURCE,
        flags=re.MULTILINE,
    )
    assert count == 0
    assert patched == PATCHED_KANBAN_GOAL_LOOP_SOURCE


@pytest.mark.parametrize("status", ACTIVE_STATUSES)
def test_idempotent_create_upgrades_active_same_slot(status: str) -> None:
    conn = _task_db(status=status)

    task_id = _patched_create_task()(
        conn,
        idempotency_key="same-slot",
        goal_mode=True,
        goal_max_turns=3,
    )

    assert task_id == "existing"
    assert _goal_fields(conn) == (1, 3)


def test_idempotent_create_preserves_existing_max_when_new_max_is_null() -> None:
    conn = _task_db(status="blocked", goal_max_turns=5)

    _patched_create_task()(
        conn,
        idempotency_key="same-slot",
        goal_mode=True,
        goal_max_turns=None,
    )

    assert _goal_fields(conn) == (1, 5)


def test_idempotent_create_does_not_reconcile_running_task() -> None:
    conn = _task_db(status="running", goal_max_turns=5)

    assert (
        _patched_create_task()(
            conn,
            idempotency_key="same-slot",
            goal_mode=True,
            goal_max_turns=3,
        )
        == "existing"
    )
    assert _goal_fields(conn) == (0, 5)


@pytest.mark.parametrize("status", ("done", "archived"))
def test_idempotent_create_does_not_mutate_terminal_history(status: str) -> None:
    conn = _task_db(status=status, goal_max_turns=5)

    if status == "archived":
        with pytest.raises(RuntimeError, match="insert path"):
            _patched_create_task()(
                conn,
                idempotency_key="same-slot",
                goal_mode=True,
                goal_max_turns=3,
            )
    else:
        assert (
            _patched_create_task()(
                conn,
                idempotency_key="same-slot",
                goal_mode=True,
                goal_max_turns=3,
            )
            == "existing"
        )

    assert _goal_fields(conn) == (0, 5)
