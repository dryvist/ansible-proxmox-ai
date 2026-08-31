"""Shared fixtures for hermes_agent goal-mode tests.

The pinned-source patch tests reassemble upstream Hermes source snippets and
run the role's own ansible.builtin.replace/blockinfile patches against them,
so the same PATCHED_* constants and _apply_runtime_patch/_task helpers are
needed by every test module in this file's original split — kept here rather
than duplicated so a patch task rename only needs updating once.

The verbatim upstream snippets themselves live in _pinned_sources.py and are
re-exported here, so `from conftest import PINNED_*` keeps working. They were
split out because a version bump only ever edits those strings, while the
fixtures below change for unrelated reasons — and because this file was over
its token budget, whose remedy is splitting, never a bigger budget.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
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
from _role_files import role_defaults, role_tasks, role_tasks_text


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
ACTIVE_STATUSES = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "blocked",
    "review",
)


def _task(name: str) -> dict[str, Any]:
    tasks = role_tasks(ROLE_ROOT)
    return next(item for item in tasks if item.get("name") == name)


def _replace_task(name: str) -> dict[str, str]:
    return _task(name)["ansible.builtin.replace"]


def _apply_runtime_patch(name: str, source: str) -> str:
    config = _replace_task(name)
    patched, count = re.subn(
        config["regexp"],
        config["replace"],
        source,
        flags=re.MULTILINE,
    )
    assert count == 1
    return patched


def _apply_rendered_runtime_patch(name: str, source: str) -> str:
    """Apply a replace task whose payload is rendered from its task vars."""
    task = _task(name)
    config = task["ansible.builtin.replace"]
    replacement = Environment(autoescape=False).from_string(config["replace"]).render(
        **task.get("vars", {})
    )
    patched, count = re.subn(
        config["regexp"], replacement, source, flags=re.MULTILINE
    )
    assert count == 1
    return patched


# Derived by running the role's own patch over the pinned upstream line,
# never hand-written — a hand-copied "expected" string can drift from what
# the role actually produces and would assert against itself.
# Upstream restructured this scan away; the clamp patch is retired and the
# assertion inverted, so the "patched" source is simply source without the
# unbounded reverse scan in it.
PATCHED_COMPRESSOR_SCAN_SOURCE = (
    "        for idx in range(start, end):\n"
)
PATCHED_CRON_DELIVERY_SOURCE = (
    # Code lives in the task's vars; `block:` is only an indent expression.
    _task("Rebind the built-in memory store for cron agents")["vars"][
        "_hermes_cron_memory_block"
    ]
    # The output-validity guard's own def line, needed for the postcondition
    # that checks it landed. Raw block text (unrendered Jinja placeholders
    # and all) — the postcondition only substring-matches the def line, which
    # carries no templating, so rendering is unnecessary here; the guard's
    # actual runtime behaviour is exec'd and rendered separately in
    # test_cron_output_validity.py.
    + _task("Patch Hermes cron delivery with an output-validity guard")[
        "ansible.builtin.blockinfile"
    ]["block"]
    # Upstream's own silence-matcher def line, pinned by
    # patches_verify.yml so a version bump that drops it fails loudly rather
    # than NameError-ing the guard above at runtime.
    + '\ndef _is_cron_silence_response(text: str) -> bool:\n'
    + _apply_runtime_patch(
        "Route failed cron deliveries from the exception path to the issues channel",
        _apply_runtime_patch(
            "Route failed cron deliveries to the issues channel",
            _apply_runtime_patch(
                "Route cron delivery content through the output-validity guard",
                _apply_runtime_patch(
                    "Route cron delivery content through the markup guard",
                    PINNED_CRON_DELIVERY_SOURCE,
                ),
            ),
        ),
    )
)
# cron/scheduler.py carries the opt-in goal-mode runner too, and
# patches_verify.yml asserts on all of it against ONE source string — so this
# snippet has to represent every scheduler patch, not just the delivery pair.
# Built by running the real patch tasks, never by pasting their expected
# output: a hand-copied snippet is what let seven dead patches stay green.
PATCHED_CRON_DELIVERY_SOURCE += (
    _task("Patch Hermes cron scheduler with an opt-in goal-mode runner")[
        "ansible.builtin.blockinfile"
    ]["block"]
)
# Production applies the goal-mode submit replacement before the wall-clock
# patch rewrites the adjacent context line. Model that exact sequence on one
# source snippet: appending separately patched copies would leave the original
# direct submit in this synthetic module even though it is absent after a real
# converge.
PATCHED_CRON_TIMEOUT_SOURCE = _apply_runtime_patch(
    "Route the cron conversation through the goal-mode runner",
    PINNED_CRON_TIMEOUT_SOURCE,
)
for _cron_timeout_task_name in (
    "Resolve the aggregate cron wall clock beside the inactivity timeout",
    "Start the aggregate cron clock before submitting the conversation",
    "Initialize the independent cron timeout result flags",
    "Keep polling whenever either cron deadline is enabled",
    "Bound the final cron poll to the exact remaining wall budget",
    "Enforce the aggregate cron wall clock in the native poll loop",
    "Guard the native inactivity comparison when that detector is disabled",
    "Raise the aggregate cron timeout before the inactivity handler",
):
    PATCHED_CRON_TIMEOUT_SOURCE = _apply_rendered_runtime_patch(
        _cron_timeout_task_name, PATCHED_CRON_TIMEOUT_SOURCE
    )
PATCHED_CRON_DELIVERY_SOURCE += (
    _task("Add aggregate cron wall-clock helpers")["ansible.builtin.blockinfile"][
        "block"
    ]
    + PATCHED_CRON_TIMEOUT_SOURCE
)
PATCHED_HINDSIGHT_PREFETCH_SOURCE = _apply_runtime_patch(
    "Patch Hermes auto-recall prefetch failure to log at warning, not debug",
    PINNED_HINDSIGHT_PREFETCH_SOURCE,
)
# Current upstream dropped the line entirely — also a passing state, since the
# assertion is now "the debug form is absent".
UPSTREAM_HINDSIGHT_PREFETCH_LINE_REMOVED = ""
PATCHED_RUN_AGENT_SOURCE = PINNED_SYNC_EXTERNAL_MEMORY_SOURCE
for _run_agent_task_name in (
    'Patch _sync_external_memory_for_turn to log its "interrupted" skip',
    "Patch _sync_external_memory_for_turn to log its missing-input skip",
    "Patch _sync_external_memory_for_turn to log its empty-flatten skip",
    "Patch _sync_external_memory_for_turn to log its swallowed exception",
):
    PATCHED_RUN_AGENT_SOURCE = _apply_runtime_patch(_run_agent_task_name, PATCHED_RUN_AGENT_SOURCE)
PATCHED_KANBAN_GOAL_LOOP_SOURCE = _apply_runtime_patch(
    "Patch Hermes kanban goal loop to retry judge errors without burning turns",
    _apply_runtime_patch(
        "Patch Hermes kanban goal loop to count consecutive judge failures",
        PINNED_KANBAN_GOAL_LOOP_SOURCE,
    ),
)
PATCHED_JUDGE_CALL_SOURCE = _apply_runtime_patch(
    "Patch Hermes goal judge to emit its model call latency",
    _apply_runtime_patch(
        "Patch Hermes goal judge to time its model call",
        PINNED_JUDGE_CALL_SOURCE,
    ),
)
PATCHED_JUDGE_AVAILABLE_SOURCE = _apply_runtime_patch(
    "Patch Hermes goal-judge availability probe to log why it declines",
    PINNED_JUDGE_AVAILABLE_SOURCE,
)
PATCHED_GOAL_JUDGE_SOURCE = (
    "DEFAULT_JUDGE_TIMEOUT = 60.0\n"
    + PINNED_JUDGE_ERROR_SENTINEL_SOURCE
    + PATCHED_KANBAN_GOAL_LOOP_SOURCE
    + PATCHED_JUDGE_CALL_SOURCE
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
    """Records every turn and the history it was handed."""

    def __init__(self) -> None:
        self.turns = 0
        self.histories: list[Any] = []

    def run_conversation(self, message, conversation_history=None):
        self.turns += 1
        self.histories.append(conversation_history)
        return {
            "final_response": f"resp{self.turns}",
            "messages": [f"m{self.turns}"],
            "completed": True,
            "failed": False,
        }
