from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
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


# test_enqueuer_goal_flags_follow_the_role_toggle DELETED (native-cron
# reframe, 18/18): kanban-enqueue-recurring.sh.j2 and every Kanban card
# (including docs-sync) are gone — there is no enqueuer template and no
# per-card `channel:` override left to test at all. hermes_agent_kanban_cards
# no longer exists; hermes_agent_kanban_goal_mode still governs ad-hoc/
# follow-up kanban work (the reviewer job filing a gap card, etc.), asserted
# elsewhere in this file against the Python patches, not against a template.
#
# test_the_kanban_card_body_still_carries_the_evidence_and_block_contract was
# removed: kanban-card-body.md.j2 no longer exists (18/18 to cron). Its
# kanban_block(kind=needs_input) escalation and self-directed `hermes send`
# instruction were Kanban-task machinery — a cron job has no task to block and
# already delivers natively via `--deliver`, so neither applies. The one piece
# of real value in that wrapper, the evidence-contract anti-fabrication
# instruction (cite the query, never invent a number), is NOT reproduced
# anywhere for the 18 converted jobs — flagged in the PR, not silently lost.


def test_reviewer_prompt_carries_no_leftover_self_perpetuation() -> None:
    """The native-cron redesign made the reviewer's own next-occurrence
    pre-create (create the next slot blocked, have the enqueuer unblock it)
    unnecessary: the crontab/cron entry is now the time gate for every job,
    reviewer included — it is a plain hermes_agent_direct_cron_jobs entry, not
    a kanban card. Pins that the chain-continuation step actually left the
    prompt, rather than merely stopped being tested: no goal_mode Jinja
    conditional exists in it at all any more, so it renders identically
    regardless of hermes_agent_kanban_goal_mode.
    """
    defaults = role_defaults(ROLE_ROOT)
    prompt = str(defaults["hermes_agent_reviewer_card_prompt"])
    assert "{%" not in prompt, "no Jinja conditionals should remain in the reviewer prompt"
    assert "initial_status=blocked" not in prompt
    assert "goal_mode" not in prompt
    assert "bounded goal loop" not in prompt
    assert prompt.strip().endswith(
        'Save the updated gap fingerprint back to "review-last".'
    )


def test_hermes_inference_paths_use_the_declared_alias() -> None:
    defaults = role_defaults(ROLE_ROOT)
    group_vars = yaml.safe_load((REPO_ROOT / "inventory/group_vars/all.yml").read_text())
    hindsight_group_vars = yaml.safe_load(
        (REPO_ROOT / "inventory/group_vars/hindsight_group.yml").read_text()
    )
    hindsight_compose = (
        REPO_ROOT / "roles/hindsight_docker/templates/docker-compose.yml.j2"
    ).read_text()
    router_defaults = role_defaults(REPO_ROOT / "roles" / "llm_router")
    registry = yaml.safe_load((REPO_ROOT / "llm-models.yml").read_text())[
        "llm_router_model_registry"
    ]
    router_config = (REPO_ROOT / "roles/llm_router/templates/config.yaml.j2").read_text()
    config = (ROLE_ROOT / "templates" / "config.yaml.j2").read_text()

    hermes_alias = "hermes-default"
    # Physical ids live in ONE file — the repo-root llm-models.yml registry —
    # and the router's selector vars are projections of it. Pinning literals
    # here is what let all four aliases drift to unroutable models at once
    # (2026-07-28, every one a live 404), so follow the indirection to its
    # source instead of re-pinning the ids under a new name.
    by_role = {
        entry["serving_role"]: entry
        for entry in registry
        if entry.get("enabled") and "serving_role" in entry
    }
    hermes_backend = by_role["primary"]["client_model_id"]
    judge_backend = by_role["small"]["client_model_id"]
    assert group_vars["hermes_brain_model"] == hermes_alias
    # The judge rides its own alias now — a judge on the worker's model is
    # self-preference bias, and the two serialize against one serving slot.
    assert group_vars["hermes_goal_judge_model"] == "goal-judge"
    assert judge_backend != hermes_backend
    assert defaults["hermes_agent_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_compression_model"] == "{{ hermes_brain_model }}"
    assert defaults["hermes_agent_memory_llm_model"] == "{{ hermes_brain_model }}"
    assert hindsight_group_vars["hindsight_docker_llm_model"] == "{{ hermes_brain_model }}"
    assert 'HINDSIGHT_API_LLM_MODEL: "{{ hindsight_docker_llm_model }}"' in hindsight_compose
    assert defaults["hermes_agent_model_max_tokens"] == 8192
    assert defaults["hermes_agent_context_compression_threshold"] == 0.75
    assert defaults["hermes_agent_brain_sync_enabled"] is False
    # An alias belongs to the entry it points at, so the whole consumer-facing
    # name set is readable off the registry — and cannot name a model that is
    # not there. A model_list deployment entry named after an alias is banned
    # (AGENTS.md): the duplicate config drifts from the real backend every time
    # the model changes.
    aliases = {
        alias: entry["client_model_id"]
        for entry in registry
        if entry.get("enabled")
        for alias in entry.get("stable_aliases", [])
    }
    assert aliases == {
        hermes_alias: hermes_backend,
        "tool-calling": hermes_backend,
        "goal-judge": judge_backend,
        "interim-brain": hermes_backend,
    }
    # Both selectors must be declared servable, or the alias indirection just
    # moves the 404 one level down.
    #
    # The cluster model is servable ONLY while the cluster leg is actually
    # available. It is hermes-default's router_settings.fallbacks target while
    # a cluster window is up, and an unroutable fallback target 502s instead of
    # failing over — which is exactly what it did, unnoticed, from 2026-08-05
    # (both hosts' clusterMode disabled, TB cable out) until #365.
    #
    # Derive the expectation from llm_router_cluster_leg_available rather than
    # re-pinning a literal: that var is the single switch #365 introduced, and
    # roles/llm_router/tasks/assert-cluster-leg.yml already fails the converge
    # if it and the registry's `servable` disagree. Following it here means
    # this test tracks the leg coming back instead of going red the moment it
    # does — re-pinning a literal is the drift this whole indirection exists
    # to prevent.
    #
    # `servable` is deliberately NOT `enabled`: every large-tier entry is
    # enabled (the router offers it), only these are servable (the backend
    # answers for it). Conflating them yields a 404, not an answer.
    expected_servable = [hermes_backend, judge_backend]
    if router_defaults["llm_router_cluster_leg_available"]:
        expected_servable.append(by_role["cluster"]["client_model_id"])
    assert [
        entry["client_model_id"] for entry in registry if entry.get("servable")
    ] == expected_servable
    hermes_entries = [
        entry for entry in registry if entry["client_model_id"] == hermes_backend
    ]
    assert len(hermes_entries) == 1
    assert hermes_entries[0]["context_window"] == 65536
    # The registry is the SOLE spelling of a model name or key field: the role's
    # defaults project it and must never re-type one. A literal here is exactly
    # the drift this indirection exists to prevent, so it fails the build rather
    # than waiting for a live 404. Values only — the defaults' prose may of
    # course still discuss the tiers.
    router_defaults_values = yaml.dump(router_defaults, allow_unicode=True)
    for entry in registry:
        for field in ("client_model_id", "upstream_model_id", "key_field"):
            if field in entry:
                assert entry[field] not in router_defaults_values, (
                    f"{entry[field]} is re-typed in roles/llm_router/defaults/main.yml; "
                    "derive it from llm-models.yml instead"
                )
    assert router_defaults["llm_router_num_retries"] == 0
    # 429 = "the slot is busy", never "the work is impossible", so the router
    # absorbs it rather than failing the caller (#175). Not 0 — that setting
    # killed a cron mid-generation on 2026-07-24.
    assert router_defaults["llm_router_rate_limit_retries"] == 8
    assert "model_group_alias:" in router_config
    assert "llm_router_model_group_aliases.items()" in router_config
    # Still pinned to the worker model, deliberately, per the measured
    # blocker recorded at length in defaults/main.yml: the `goal-judge` alias
    # is live and its DESIGN is correct (same var this test already asserts,
    # hermes_goal_judge_model == "goal-judge"), but its backend is llama-swap
    # swap-class in the deployed config (nix-ai
    # modules/mlx/llama-swap-topology.nix — worker pinned ttl=0, judge
    # ttl=900), and a measured ~79s cold load would exceed the judge timeout.
    # Flipping this to "{{ hermes_goal_judge_model }}" without that residency
    # fix landing first breaks card judgement — measured live 2026-08, not
    # theoretical. Do not flip it back without re-measuring.
    #
    # The timeout below is unrelated: it is raised on MEASURED tail latency
    # of hermes_agent_model itself (the pinned model the judge actually
    # calls today), not on the cold-load case above — see defaults/main.yml's
    # ADDENDUM comment for the full figures.
    assert defaults["hermes_agent_kanban_goal_judge_model"] == "{{ hermes_agent_model }}"
    assert defaults["hermes_agent_kanban_goal_judge_timeout_seconds"] == 150
    assert "goal_judge:" in config
    assert "model: {{ hermes_agent_kanban_goal_judge_model | to_json }}" in config
    assert "base_url: '{{ hermes_agent_model_base_url }}'" in config


def test_group_vars_reads_canonical_zammad_mcp_pair() -> None:
    group_vars = (REPO_ROOT / "inventory/group_vars/hermes_agent_group.yml").read_text()
    assert "bao_local_llm_secrets.ZAMMAD_MCP_URL" in group_vars
    assert "bao_local_llm_secrets.ZAMMAD_MCP_TOKEN" in group_vars
    assert "bao_local_llm_secrets.ZAMMAD_API_TOKEN" not in group_vars
    assert "ZAMMAD_MCP_URL | regex_replace('/api/v1/?$', '')" in group_vars
    assert "else lookup('env', 'ZAMMAD_URL')" in group_vars


def test_prompt_catalog_build_keeps_a_gc_root() -> None:
    build_task = _task("Build the pinned prompt catalog on the controller")
    command = build_task["ansible.builtin.command"]["cmd"]
    assert "--out-link /tmp/hermes-agent-prompts" in command
    assert "--no-link" not in command


def test_installed_source_postconditions_fail_closed() -> None:
    read_task = _task("Read installed Hermes pinned-source patches")
    assert "ansible.builtin.slurp" in read_task
    assert read_task["register"] == "hermes_agent_goal_mode_sources"
    # The assert task indexes results[] positionally, so the slurp order is
    # load-bearing: a file inserted anywhere but the end silently re-points
    # every later assertion at the wrong source.
    assert [path.split("}}/")[-1] for path in read_task["loop"]] == [
        "tools/kanban_tools.py",
        "hermes_cli/kanban_db.py",
        "hermes_cli/goals.py",
        "agent/conversation_loop.py",
        "agent/auxiliary_client.py",
        "agent/context_compressor.py",
        "cron/scheduler.py",
        "plugins/memory/hindsight/__init__.py",
        "run_agent.py",
    ]

    assert_task = _task("Assert installed Hermes pinned-source patches")
    conditions = " ".join(assert_task["ansible.builtin.assert"]["that"])
    assert "verdict, reason, _, _ = judge_goal(" in conditions
    assert "DEFAULT_JUDGE_TIMEOUT =" in conditions
    assert "SELECT id, status FROM tasks" in conditions
    assert (
        'if goal_mode and row["status"] in ("triage", "todo", "scheduled", '
        '"ready", "blocked", "review"):'
        in conditions
    )
    assert "goal_max_turns = COALESCE(?, goal_max_turns)" in conditions
    assert any(
        '*(["--quiet"] if task.goal_mode else []),' in condition
        and ".count(" in condition
        and ") == 1" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert any(
        '"-q", prompt,' in condition
        and ".count(" in condition
        and ") == 1" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert '["--quiet"]' in conditions
    assert "WHERE id = ? AND status IN" in conditions
    assert "_TRANSIENT_RETRY_BACKOFF_BASE = 15.0" in conditions
    assert "status in (408, 429)" in conditions
    assert "for idx in range(min(end, len(messages)) - 1, start - 1, -1):" in conditions
    assert "deliver_content = _cron_markup_guard(job, output_file," in conditions
    # A failed run must reach the issues channel, not the work surface.
    assert "_deliver_result(_routed_job, deliver_content," in conditions
    assert (
        "registered for dispatcher-spawned workers (HERMES_KANBAN_TASK "
        in conditions
    )
    # The message and the retry rule are asserted as a pair: the message tells
    # the operator the card will be retried, so it must not be able to land
    # while the forced first-failure give-up is still in the source.
    assert (
        "a missing toolset. The card is retried within its own max_retries"
        in conditions
    )
    assert "failure_limit=1 if is_systemic else None," in conditions
    assert (
        'logger.warning("Hindsight prefetch failed: %s", e, exc_info=True)'
        in conditions
    )
    assert 'if reason.startswith("judge error: "):' in conditions
    assert "blocked_judge_unreachable" in conditions
    # The upstream sentinel producer must stay pinned: reworded upstream, the
    # guard would silently never fire.
    assert (
        'return "continue", f"judge error: {type(exc).__name__}", False, None'
        in conditions
    )
    assert any(
        "judge_failures = 0" in condition
        and ".count(" in condition
        and ") == 2" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert any(
        "_boost_cap = agent.max_tokens if agent.max_tokens else max(" in condition
        and ".count(" in condition
        and ") == 2" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert 'resolved_provider != "custom"' in conditions
    assert "update the pinned-source patches" in assert_task["ansible.builtin.assert"][
        "fail_msg"
    ]

    completion_source = _apply_runtime_patch(
        "Patch Hermes goal completion gate for the four-value judge result",
        PINNED_GOAL_COMPLETION_SOURCE,
    )
    reconcile_source = _apply_runtime_patch(
        "Patch Hermes idempotent create to reconcile goal-mode fields",
        PINNED_CREATE_TASK_SOURCE,
    )
    reconcile_source = (
        _apply_runtime_patch(
            "Patch Hermes Kanban workers to enter the quiet goal-loop path",
            PINNED_WORKER_SPAWN_SOURCE,
        )
        + reconcile_source
        + _apply_runtime_patch(
            "Patch Hermes protocol-violation message to name the "
            "model-did-not-call case",
            PINNED_PROTOCOL_VIOLATION_SOURCE,
        )
        + _apply_runtime_patch(
            "Patch Hermes protocol-violation crashes to retry within the card budget",
            PINNED_PROTOCOL_RETRY_SOURCE,
        )
    )
    worker_reap_source = PINNED_WORKER_REAP_SOURCE
    for patch_name in (
        "Patch Hermes worker-reap timeout path to verify PID safety before signaling",
        "Patch Hermes worker-reap timeout path to signal the worker's process group",
        "Patch Hermes worker-reap timeout path to escalate on the worker's process group",
    ):
        worker_reap_source = _apply_runtime_patch(patch_name, worker_reap_source)
    # The blockinfile-inserted identity-check helper the three replaces
    # above call into — landed separately from them at converge time, so
    # the assert conditions checking for it need it present here too.
    worker_reap_source = (
        _task(
            "Patch Hermes worker-reap helper to verify PID identity before signaling"
        )["ansible.builtin.blockinfile"]["block"]
        + worker_reap_source
    )
    reconcile_source += worker_reap_source

    stale_reclaim_source = PINNED_STALE_RECLAIM_TERMINATE_SOURCE
    for patch_name in (
        "Patch Hermes stale-reclaim worker termination to verify PID safety before signaling",
        "Patch Hermes stale-reclaim worker termination to signal the worker's process group",
        "Patch Hermes stale-reclaim worker termination to escalate on the worker's process group",
    ):
        stale_reclaim_source = _apply_runtime_patch(patch_name, stale_reclaim_source)
    # The blockinfile-inserted identity-check helper both the timeout-path
    # and stale-reclaim-path signal sites call into.
    stale_reclaim_source = (
        _task(
            "Patch Hermes worker-reap helper to verify PID identity before signaling"
        )["ansible.builtin.blockinfile"]["block"]
        + stale_reclaim_source
    )
    reconcile_source += stale_reclaim_source
    # The six client-side backoff hacks are gone (see
    # test_client_side_backoff_hacks_stay_reverted); only the two kept
    # max_tokens-ceiling patches still contribute to this source.
    retry_source = _apply_runtime_patch(
        "Patch hermes-agent retry boost to respect the configured max_tokens ceiling",
        PINNED_TC_BOOST_CAP_SOURCE,
    )
    retry_source += _apply_runtime_patch(
        "Patch hermes-agent length-continuation boost to respect the configured "
        "max_tokens ceiling",
        PINNED_BOOST_CAP_SOURCE,
    )
    auxiliary_source = "\n".join(
        (
            "_TRANSIENT_RETRY_BACKOFF_BASE = 15.0",
            "return isinstance(status, int) and (status in (408, 429) or 500 <= status < 600)",
            'if should_fallback and (is_auto or (is_capacity_error and resolved_provider != "custom")):',
        )
    )
    assert all(
        _source_postconditions(
            completion_source, reconcile_source, retry_source, auxiliary_source
        )
    )
    assert not all(
        _source_postconditions(
            PINNED_GOAL_COMPLETION_SOURCE,
            PINNED_CREATE_TASK_SOURCE,
            "",
            "",
        )
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source.replace("COALESCE(?, goal_max_turns)", "?"),
            retry_source,
            auxiliary_source,
        )
    )
    # Each newly-covered patch, dropped one at a time. Without these the
    # assertions could be decorative — present in the task file, but
    # incapable of going red on the failure they exist to catch.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            compressor_source=PINNED_COMPRESSOR_SCAN_SOURCE,
        )
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            cron_scheduler_source=PINNED_CRON_DELIVERY_SOURCE,
        )
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source.replace(
                "registered for dispatcher-spawned workers (HERMES_KANBAN_TASK ",
                "",
            ),
            retry_source,
            auxiliary_source,
        )
    )
    # The forced first-failure give-up left in place: a protocol violation
    # would still retire the card on its first occurrence, so the postconditions
    # must go red even though the reworded message landed.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source.replace(
                "failure_limit=1 if is_systemic else None,",
                "failure_limit=1 if (protocol_violation or is_systemic) else None,",
            ),
            retry_source,
            auxiliary_source,
        )
    )
    # The judge-error guard dropped (upstream loop unpatched): must go red
    # even though the sentinel producer is still present.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            goal_judge_source=(
                "DEFAULT_JUDGE_TIMEOUT = 60.0\n"
                + PINNED_JUDGE_ERROR_SENTINEL_SOURCE
                + PINNED_KANBAN_GOAL_LOOP_SOURCE
            ),
        )
    )
    # The upstream sentinel producer drifted out from under the guard: the
    # patched loop alone must not satisfy the postconditions.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            goal_judge_source=(
                "DEFAULT_JUDGE_TIMEOUT = 60.0\n" + PATCHED_KANBAN_GOAL_LOOP_SOURCE
            ),
        )
    )
    # The length-continuation boost dropped while the _tc_ one landed. The
    # needle `_boost_cap = agent.max_tokens ...` still occurs once (inside
    # the _tc_boost_cap line), so only the ==2 count catches this — a plain
    # substring assertion would pass here with the patch missing.
    retry_source_tc_only = retry_source.replace(
        "_boost_cap = agent.max_tokens if agent.max_tokens else max("
        "32768, _requested_cap or 0)",
        "_boost_cap = max(32768, _requested_cap or 0)",
    )
    assert "_tc_boost_cap = agent.max_tokens" in retry_source_tc_only
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source_tc_only,
            auxiliary_source,
        )
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            hindsight_plugin_source=PINNED_HINDSIGHT_PREFETCH_SOURCE,
        )
    )
    # Worker-reap process-group guard dropped: reconcile_source reverts to
    # not carrying the patched reap at all — must go red.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source.replace(worker_reap_source, ""),
            retry_source,
            auxiliary_source,
        )
    )
    # Stale-reclaim process-group guard dropped: reconcile_source reverts to
    # not carrying the patched reclaim termination at all — must go red.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source.replace(stale_reclaim_source, ""),
            retry_source,
            auxiliary_source,
        )
    )
