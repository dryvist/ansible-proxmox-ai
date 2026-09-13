"""Shared fixtures for hermes_agent goal-mode tests.

The pinned-source patch tests reassemble upstream Hermes source snippets and
run the role's own ansible.builtin.replace/blockinfile patches against them,
so the same PATCHED_* constants and _apply_runtime_patch/_task helpers are
needed by every test module in this file's original split — kept here rather
than duplicated so a patch task rename only needs updating once.

The verbatim upstream snippets themselves live in _pinned_sources.py and the
PATCHED_* constants derived from them (plus the _task/_apply_runtime_patch
helpers and REPO_ROOT/ROLE_ROOT) live in _patched_sources.py, both
re-exported here so `from conftest import PINNED_*`/`PATCHED_*` keeps
working. Split out because a version bump only ever edits those strings,
while the fixtures below change for unrelated reasons — and because this
file was over its token budget, whose remedy is splitting, never a bigger
budget.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

import yaml
from jinja2 import Environment

from _pinned_sources import (
    JUDGE_ERROR,
    PINNED_BOOST_CAP_SOURCE,
    PINNED_COMPRESSOR_SCAN_SOURCE,
    PINNED_CREATE_TASK_SOURCE,
    PINNED_CRON_DELIVERY_SOURCE,
    PINNED_CRON_SUBMIT_SOURCE,
    PINNED_CRON_TIMEOUT_SOURCE,
    PINNED_GOAL_COMPLETION_SOURCE,
    PINNED_HINDSIGHT_PREFETCH_SOURCE,
    PINNED_JUDGE_AVAILABLE_SOURCE,
    PINNED_JUDGE_CALL_SOURCE,
    PINNED_JUDGE_ERROR_SENTINEL_SOURCE,
    PINNED_PROTOCOL_RETRY_SOURCE,
    PINNED_PROTOCOL_VIOLATION_SOURCE,
    PINNED_STALE_RECLAIM_TERMINATE_SOURCE,
    PINNED_SYNC_EXTERNAL_MEMORY_SOURCE,
    PINNED_TC_BOOST_CAP_SOURCE,
    PINNED_WORKER_REAP_SOURCE,
    PINNED_WORKER_SPAWN_SOURCE,
)
from _pinned_goal_loop import (
    KANBAN_GOAL_CONTINUATION_TEMPLATE,
    KANBAN_GOAL_FINALIZE_TEMPLATE,
    PINNED_KANBAN_GOAL_LOOP_SOURCE,
)
from _patched_sources import (
    REPO_ROOT,
    ROLE_ROOT,
    PATCHED_COMPRESSOR_SCAN_SOURCE,
    PATCHED_CRON_DELIVERY_SOURCE,
    PATCHED_CRON_TIMEOUT_SOURCE,
    PATCHED_GOAL_JUDGE_SOURCE,
    PATCHED_HINDSIGHT_PREFETCH_SOURCE,
    PATCHED_JUDGE_AVAILABLE_SOURCE,
    PATCHED_JUDGE_CALL_SOURCE,
    PATCHED_KANBAN_GOAL_LOOP_SOURCE,
    PATCHED_RUN_AGENT_SOURCE,
    UPSTREAM_HINDSIGHT_PREFETCH_LINE_REMOVED,
    _apply_rendered_runtime_patch,
    _apply_runtime_patch,
    _replace_task,
    _task,
)
from _role_files import role_defaults, role_tasks, role_tasks_text


ACTIVE_STATUSES = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "blocked",
    "review",
)


class _FakeTime:
    """Records sleeps instead of taking them."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _patched_goal_loop(judge_results: list[tuple]) -> tuple[Any, _FakeTime]:
    """Exec the patched verbatim loop with a scripted judge and fake clock."""
    results = iter(judge_results)

    def judge_goal(goal: str, response: str) -> tuple:
        return next(results, ("continue", "keep going", False, None, False))

    fake_time = _FakeTime()
    namespace: dict[str, Any] = {
        # Upstream module-level names the verbatim loop closes over. The old
        # hand-reduced copy inlined these; a verbatim one must be given them.
        "DEFAULT_MAX_TURNS": 8,
        "KANBAN_GOAL_CONTINUATION_TEMPLATE": "continue: {reason}",
        "KANBAN_GOAL_FINALIZE_TEMPLATE": "finalize: {reason}",
        "Dict": dict,
        "Any": object,
        "judge_goal": judge_goal,
        "_truncate": lambda text, limit: text,
        "time": fake_time,
    }
    exec(PATCHED_KANBAN_GOAL_LOOP_SOURCE, namespace)
    return namespace["run_kanban_goal_loop"], fake_time


@contextmanager
def _write_txn(conn: sqlite3.Connection) -> Iterator[None]:
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _patched_create_task() -> Any:
    source = _apply_runtime_patch(
        "Patch Hermes idempotent create to reconcile goal-mode fields",
        PINNED_CREATE_TASK_SOURCE,
    )
    namespace: dict[str, Any] = {"write_txn": _write_txn}
    exec(source, namespace)
    return namespace["create_task"]


def _task_db(
    *, status: str, goal_mode: int = 0, goal_max_turns: int | None = None
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tasks ("
        "id TEXT PRIMARY KEY, idempotency_key TEXT, status TEXT, created_at INTEGER, "
        "goal_mode INTEGER, goal_max_turns INTEGER)"
    )
    conn.execute(
        "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
        ("existing", "same-slot", status, 1, goal_mode, goal_max_turns),
    )
    conn.commit()
    return conn


def _goal_fields(conn: sqlite3.Connection) -> tuple[int, int | None]:
    row = conn.execute(
        "SELECT goal_mode, goal_max_turns FROM tasks WHERE id = 'existing'"
    ).fetchone()
    return row["goal_mode"], row["goal_max_turns"]


# The cron CLI exit-code patch is a one-line `replace`, so the fixture states
# both forms directly rather than re-deriving them: upstream drops the return
# value, the patch hands it back.
PINNED_CLI_MAIN_SOURCE = (
    "def cmd_cron(args):\n"
    '    """Cron job management."""\n'
    "    from hermes_cli.cron import cron_command\n"
    "\n"
    "    cron_command(args)\n"
)
PATCHED_CLI_MAIN_SOURCE = PINNED_CLI_MAIN_SOURCE.replace(
    "    cron_command(args)", "    return cron_command(args)"
)
# Upstream's September 2026 decomposition moved worker-reap process-group
# guarding out of kanban_db.py into kanban_db_dispatch.py and centralized
# both reapers' SIGKILL escalation onto one shared _sigkill(kill, pid)
# helper, so patches_verify.yml checks all of it against ONE dedicated
# hermes_agent_kanban_dispatch_source var, not hermes_agent_goal_reconcile_
# source. Built the same way as every other PATCHED_* constant here: run
# the role's own patch tasks over minimal real fragments, never hand-copy
# the expected output.
PATCHED_KANBAN_DISPATCH_SOURCE = (
    _task("Patch Hermes worker-reap helper to verify PID identity before signaling")[
        "ansible.builtin.blockinfile"
    ]["block"]
    + _apply_runtime_patch(
        "Patch Hermes worker-reap SIGKILL escalation to signal the worker's process group",
        _apply_runtime_patch(
            "Patch Hermes worker-reap timeout path to signal the worker's process group",
            _apply_runtime_patch(
                "Patch Hermes worker-reap timeout path to verify PID safety before signaling",
                PINNED_WORKER_REAP_SOURCE,
            ),
        ),
    )
    + _apply_runtime_patch(
        "Patch Hermes worker-reap SIGKILL escalation to signal the worker's process group",
        _apply_runtime_patch(
            "Patch Hermes stale-reclaim worker termination to signal the worker's process group",
            _apply_runtime_patch(
                "Patch Hermes stale-reclaim worker termination to verify PID safety before signaling",
                PINNED_STALE_RECLAIM_TERMINATE_SOURCE,
            ),
        ),
    )
    # Both retired protocol-violation checks also moved off kanban_db.py
    # into kanban_db_dispatch.py in the same September 2026 decomposition.
    + PINNED_PROTOCOL_VIOLATION_SOURCE
    + PINNED_PROTOCOL_RETRY_SOURCE
)


# Upstream's September 2026 decomposition also moved the length-continuation
# max_tokens-ceiling patch's target file to turn_iteration_prep.py (path
# change only, content unchanged) — checked against its own dedicated
# hermes_agent_turn_iteration_prep_source var, not hermes_agent_retry_source.
PATCHED_TURN_ITERATION_PREP_SOURCE = _apply_runtime_patch(
    "Patch hermes-agent length-continuation boost to respect the configured "
    "max_tokens ceiling",
    PINNED_BOOST_CAP_SOURCE,
)
# Upstream's September 2026 decomposition also moved the retry-boost
# max_tokens-ceiling patch's target file to turn_truncation.py and inlined
# its standalone `_tc_boost_cap =` variable into the min() call it fed —
# checked against its own dedicated hermes_agent_turn_truncation_source var,
# not hermes_agent_retry_source (now unused by any condition).
PATCHED_TURN_TRUNCATION_SOURCE = _apply_runtime_patch(
    "Patch hermes-agent retry boost to respect the configured max_tokens ceiling",
    PINNED_TC_BOOST_CAP_SOURCE,
)


def _combined_assert_task() -> dict[str, Any]:
    """Recombine the token-limit-split source-patch assert (2026-08-16) back
    into one dict, so every caller still sees the full `that:`/fail_msg it
    saw before the split — same conditions, just relocated across two files.
    """
    a = _task("Assert installed Hermes pinned-source patches")["ansible.builtin.assert"]
    b = _task("Assert installed Hermes pinned-source patches (cron/memory/judge half)")[
        "ansible.builtin.assert"
    ]
    return {
        "ansible.builtin.assert": {"that": a["that"] + b["that"], "fail_msg": a["fail_msg"]}
    }


def _source_postconditions(
    completion_source: str,
    reconcile_source: str,
    retry_source: str,
    auxiliary_source: str,
    compressor_source: str = PATCHED_COMPRESSOR_SCAN_SOURCE,
    cron_scheduler_source: str = PATCHED_CRON_DELIVERY_SOURCE,
    hindsight_plugin_source: str = PATCHED_HINDSIGHT_PREFETCH_SOURCE,
    goal_judge_source: str = PATCHED_GOAL_JUDGE_SOURCE,
    run_agent_source: str = PATCHED_RUN_AGENT_SOURCE,
    cli_main_source: str = PATCHED_CLI_MAIN_SOURCE,
    kanban_dispatch_source: str = PATCHED_KANBAN_DISPATCH_SOURCE,
    turn_iteration_prep_source: str = PATCHED_TURN_ITERATION_PREP_SOURCE,
    turn_truncation_source: str = PATCHED_TURN_TRUNCATION_SOURCE,
) -> tuple[bool, ...]:
    that = _combined_assert_task()["ansible.builtin.assert"]["that"]
    environment = Environment(autoescape=False)
    context = {
        "hermes_agent_goal_completion_source": completion_source,
        "hermes_agent_goal_reconcile_source": reconcile_source,
        "hermes_agent_goal_judge_source": goal_judge_source,
        "hermes_agent_kanban_goal_judge_timeout_seconds": 60,
        "hermes_agent_retry_source": retry_source,
        "hermes_agent_auxiliary_source": auxiliary_source,
        "hermes_agent_compressor_source": compressor_source,
        "hermes_agent_cron_scheduler_source": cron_scheduler_source,
        "hermes_agent_hindsight_plugin_source": hindsight_plugin_source,
        "hermes_agent_run_agent_source": run_agent_source,
        "hermes_agent_cli_main_source": cli_main_source,
        "hermes_agent_kanban_dispatch_source": kanban_dispatch_source,
        "hermes_agent_turn_iteration_prep_source": turn_iteration_prep_source,
        "hermes_agent_turn_truncation_source": turn_truncation_source,
    }
    return tuple(
        bool(environment.compile_expression(condition)(**context)) for condition in that
    )


BLOCK_TASK = "Patch Hermes cron scheduler with an opt-in goal-mode runner"


def _goal_runner_namespace() -> dict[str, Any]:
    """Exec the blockinfile payload in isolation and hand back its namespace."""
    block = _task(BLOCK_TASK)["ansible.builtin.blockinfile"]["block"]
    # The block is pure Python by design — no Jinja to render. If that ever
    # stops being true this assertion is the early warning, not a NameError
    # thrown from inside exec().
    assert "{{" not in block, "block gained Jinja; render it before exec"
    namespace: dict[str, Any] = {
        "os": __import__("os"),
        "logger": logging.getLogger("test.cron.goal"),
    }
    exec(compile(block, "cron-goal-block", "exec"), namespace)  # noqa: S102
    return namespace


class _StubAgent:
    """Records every turn, the history it was handed, and the task id.

    The signature mirrors the real ``AIAgent.run_conversation``, which was
    confirmed by introspecting the installed class to accept ``task_id``
    (defaulting to ``str(uuid.uuid4())`` when omitted). Keeping the stub
    narrower than the real method would let a caller that stopped passing
    ``task_id`` still pass its tests — the identifier would silently revert
    to an opaque uuid on the guest and nothing here would notice.
    """

    def __init__(self) -> None:
        self.turns = 0
        self.histories: list[Any] = []
        self.task_ids: list[Any] = []

    def run_conversation(self, message, conversation_history=None, task_id=None):
        self.turns += 1
        self.histories.append(conversation_history)
        self.task_ids.append(task_id)
        return {
            "final_response": f"resp{self.turns}",
            "messages": [f"m{self.turns}"],
            "completed": True,
            "failed": False,
        }
