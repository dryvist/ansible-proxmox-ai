"""Shared fixtures for hermes_agent goal-mode tests.

The pinned-source patch tests reassemble upstream Hermes source snippets and
run the role's own ansible.builtin.replace/blockinfile patches against them,
so the same PINNED_*/PATCHED_* constants and _apply_runtime_patch/_task
helpers are needed by every test module in this file's original split — kept
here rather than duplicated so a patch task rename only needs updating once.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml
from jinja2 import Environment

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
PINNED_CREATE_TASK_SOURCE = '''\
def create_task(conn, *, idempotency_key=None, goal_mode=False, goal_max_turns=None):
    if idempotency_key:
        row = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? "
            "AND status != 'archived' "
            "ORDER BY created_at DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        if row:
            return row["id"]
    raise RuntimeError("insert path")
'''
PINNED_GOAL_COMPLETION_SOURCE = "                    verdict, reason, _ = judge_goal(\n"
# Verbatim upstream lines at the pinned tag (v2026.7.7.2), indentation
# included — `_apply_runtime_patch` re-runs the role's own regexp against
# these, so a copy that drifts from upstream would silently stop patching
# and the test would go green on nothing.
PINNED_TC_BOOST_CAP_SOURCE = (
    "                                _tc_boost_cap = max("
    "32768, _tc_requested_cap or 0)\n"
)
PINNED_BOOST_CAP_SOURCE = (
    "            _boost_cap = max(32768, _requested_cap or 0)\n"
)
PINNED_COMPRESSOR_SCAN_SOURCE = (
    "        for idx in range(end - 1, start - 1, -1):\n"
)
PINNED_PROTOCOL_VIOLATION_SOURCE = (
    '                    "worker exited cleanly (rc=0) without calling "\n'
    '                    "kanban_complete or kanban_block — protocol violation"\n'
)
PINNED_PROTOCOL_RETRY_SOURCE = (
    "                failure_limit=1 if (protocol_violation or is_systemic) "
    "else None,\n"
)
PINNED_CRON_DELIVERY_SOURCE = (
    "            deliver_content = final_response if success else "
    "_summarize_cron_failure_for_delivery(job, error)\n"
    "                    delivery_error = _deliver_result(job, deliver_content, "
    "adapters=adapters, loop=loop)\n"
)
PINNED_WORKER_REAP_SOURCE = '''\
def _reap(pid, signal_fn=None):
    killed = False
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is not None:
        try:
            kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        for _ in range(10):
            if not _pid_alive(pid):
                break
            time.sleep(0.5)
        if _pid_alive(pid):
            try:
                _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                kill(pid, _sigkill)
                killed = True
            except (ProcessLookupError, OSError):
                pass
    return killed
'''
PINNED_STALE_RECLAIM_TERMINATE_SOURCE = '''\
def _reclaim(pid, signal_fn=None):
    info = {"terminated": False, "sigkill": False}
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is None:
        return info

    info["termination_attempted"] = True
    try:
        kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        info["terminated"] = True
        return info
    except OSError:
        return info

    for _ in range(10):
        if not _pid_alive(pid):
            info["terminated"] = True
            return info
        time.sleep(0.5)

    if _pid_alive(pid):
        try:
            _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            kill(int(pid), _sigkill)
            info["sigkill"] = True
        except (ProcessLookupError, OSError):
            return info

    info["terminated"] = not _pid_alive(pid)
    return info
'''
PINNED_WORKER_SPAWN_SOURCE = '''\
def build_worker_argv(task, prompt):
    cmd = []
    cmd.extend([
        "chat",
        "-q", prompt,
    ])
    return cmd
'''
# Verbatim upstream lines at the pinned tag (v2026.7.7.2), from
# plugins/memory/hindsight/__init__.py's queue_prefetch._run() exception
# handler — indentation included, same drift protection as the other
# PINNED_*_SOURCE fixtures above.
PINNED_HINDSIGHT_PREFETCH_SOURCE = (
    "            except Exception as e:\n"
    '                logger.debug("Hindsight prefetch failed: %s", e, exc_info=True)\n'
)
# Verbatim upstream shape of run_agent.py's _sync_external_memory_for_turn —
# indentation included, same drift protection as the other PINNED_*_SOURCE
# fixtures above. Full behavioral coverage of the four patches this feeds
# lives in test_memory_sync_observability.py; this copy exists only so
# _source_postconditions() below has something to default the new
# hermes_agent_run_agent_source context var to.
PINNED_SYNC_EXTERNAL_MEMORY_SOURCE = '''\
class _Agent:
    def _sync_external_memory_for_turn(
        self,
        *,
        original_user_message,
        final_response,
        interrupted,
        messages=None,
    ) -> None:
        if interrupted:
            return
        if not (self._memory_manager and final_response and original_user_message):
            return
        user_text = _summarize_user_message_for_log(original_user_message, sep="\\n")
        response_text = _summarize_user_message_for_log(final_response, sep="\\n")
        if not (user_text and response_text):
            return
        try:
            sync_kwargs = {"session_id": self.session_id or ""}
            if messages is not None:
                sync_kwargs["messages"] = messages
            self._memory_manager.sync_all(
                user_text,
                response_text,
                **sync_kwargs,
            )
            self._memory_manager.queue_prefetch_all(
                user_text,
                session_id=self.session_id or "",
            )
        except Exception:
            pass
'''
# Reduced run_kanban_goal_loop skeleton: the control flow that matters
# (status poll, judge, budget check, worker turn, increment) with the two
# patch anchor sites VERBATIM from upstream v2026.7.7.2 — indentation
# included, since the role's regexes capture and reuse it.
PINNED_KANBAN_GOAL_LOOP_SOURCE = '''\
def run_kanban_goal_loop(*, task_id, goal_text, run_turn, task_status_fn,
                         block_fn, max_turns=8, first_response="", log=None):
    def _log(msg):
        if log is not None:
            log(msg)

    last_response = first_response or ""
    # The first turn already consumed one unit of budget.
    turns_used = 1
    nudged_to_finalize = False

    while True:
        status = task_status_fn()
        if status == "done":
            return {"outcome": "completed_by_worker", "turns_used": turns_used, "reason": "worker completed the task"}
        if status not in ("running", "ready"):
            return {"outcome": "stopped", "turns_used": turns_used, "reason": f"status={status}"}

        verdict, reason, _parse_failed, _wait = judge_goal(goal_text, last_response)
        if verdict == "wait":
            verdict = "continue"
        _log(f"kanban goal loop: turn {turns_used}/{max_turns} verdict={verdict} reason={_truncate(reason, 120)}")

        if turns_used >= max_turns:
            block_fn("turn budget exhausted")
            return {"outcome": "blocked_budget", "turns_used": turns_used, "reason": "turn budget exhausted"}

        last_response = run_turn("continue") or ""
        turns_used += 1
'''
# Verbatim upstream producer of the "judge error: " sentinel the guard keys
# on (judge_goal's except handler). Kept pinned so the fail-closed test
# proves the converge assert goes red when upstream rewords it.
PINNED_JUDGE_ERROR_SENTINEL_SOURCE = (
    '        return "continue", f"judge error: {type(exc).__name__}", '
    "False, None\n"
)
JUDGE_ERROR = ("continue", "judge error: NotFoundError", False, None)


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


# Derived by running the role's own patch over the pinned upstream line,
# never hand-written — a hand-copied "expected" string can drift from what
# the role actually produces and would assert against itself.
PATCHED_COMPRESSOR_SCAN_SOURCE = _apply_runtime_patch(
    "Patch hermes-agent context summary scan to use the live message bound",
    PINNED_COMPRESSOR_SCAN_SOURCE,
)
PATCHED_CRON_DELIVERY_SOURCE = _apply_runtime_patch(
    "Route failed cron deliveries to the issues channel",
    _apply_runtime_patch(
        "Route cron delivery content through the markup guard",
        PINNED_CRON_DELIVERY_SOURCE,
    ),
)
PATCHED_HINDSIGHT_PREFETCH_SOURCE = _apply_runtime_patch(
    "Patch Hermes auto-recall prefetch failure to log at warning, not debug",
    PINNED_HINDSIGHT_PREFETCH_SOURCE,
)
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
PATCHED_GOAL_JUDGE_SOURCE = (
    "DEFAULT_JUDGE_TIMEOUT = 60.0\n"
    + PINNED_JUDGE_ERROR_SENTINEL_SOURCE
    + PATCHED_KANBAN_GOAL_LOOP_SOURCE
)


class _FakeTime:
    """Records sleeps instead of taking them."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _patched_goal_loop(judge_results: list[tuple]) -> tuple[Any, _FakeTime]:
    """Exec the patched reduced loop with a scripted judge and fake clock."""
    results = iter(judge_results)

    def judge_goal(goal: str, response: str) -> tuple:
        return next(results, ("continue", "keep going", False, None))

    fake_time = _FakeTime()
    namespace: dict[str, Any] = {
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
) -> tuple[bool, ...]:
    task = _task("Assert installed Hermes pinned-source patches")
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
    }
    return tuple(
        bool(environment.compile_expression(condition)(**context))
        for condition in task["ansible.builtin.assert"]["that"]
    )
