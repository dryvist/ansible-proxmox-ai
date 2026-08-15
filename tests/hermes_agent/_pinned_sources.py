"""Verbatim upstream Hermes source snippets the patch tests run against.

Split out of conftest.py: a version bump only ever edits these strings, and an
agent re-anchoring a patch should read the snippets without the fixtures, and
vice versa. Every constant is upstream source EXACTLY as shipped — never the
expected post-patch form, which is what let seven dead patches stay green.
"""

from __future__ import annotations

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
PINNED_GOAL_COMPLETION_SOURCE = "        verdict, reason, _, _, _ = judge_goal(\n"
# Verbatim upstream lines at the pinned release, indentation included. A copy
# that drifts from upstream silently stops patching and the test goes green on
# nothing — these sat at v2026.7.7.2 while the role installed a much later
# release, which is how seven patches came to match zero times with every test
# passing. Re-verify with scripts/verify-pinned-patches.py on a version bump.
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
# Upstream now supplies both protocol-violation behaviors itself, so these are
# no longer patch INPUTS — they are the upstream text the retained assertions
# pin. Both role patches were retired for matching zero times.
PINNED_PROTOCOL_VIOLATION_SOURCE = (
    '                    "without a terminal kanban call counts as failed no "\n'
)
PINNED_PROTOCOL_RETRY_SOURCE = (
    "                failure_limit=1 if is_systemic else None,\n"
)
PINNED_CRON_DELIVERY_SOURCE = (
    "            deliver_content = final_response if success else "
    "_summarize_cron_failure_for_delivery(job, error)\n"
    "                    delivery_error = _deliver_result(job, deliver_content, "
    "adapters=adapters, loop=loop)\n"
)
# Verbatim from cron/scheduler.py — the single run_conversation submit that
# opt-in cron goal mode wraps.
PINNED_CRON_SUBMIT_SOURCE = (
    "        _cron_context = contextvars.copy_context()\n"
    "        _cron_future = _cron_pool.submit(_cron_context.run,"
    " agent.run_conversation, prompt)\n"
    "        _inactivity_timeout = False\n"
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
# patch anchor sites VERBATIM from the pinned upstream release — indentation
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

        verdict, reason, _parse_failed, _wait, _transport_failed = judge_goal(goal_text, last_response)
        if verdict == "wait":
            verdict = "continue"
        _log(f"kanban goal loop: turn {turns_used}/{max_turns} verdict={verdict} reason={_truncate(reason, 120)}")

        if turns_used >= max_turns:
            block_fn("turn budget exhausted")
            return {"outcome": "blocked_budget", "turns_used": turns_used, "reason": "turn budget exhausted"}

        last_response = run_turn("continue") or ""
        turns_used += 1
'''
# Verbatim upstream producer of the transport-failure flag the guard keys on
# (judge_goal's except handler; the trailing True IS the flag). Kept pinned so
# the fail-closed test proves the converge assert goes red if upstream stops
# setting it.
PINNED_JUDGE_ERROR_SENTINEL_SOURCE = (
    '        return "continue", f"judge error: {type(exc).__name__}", '
    "False, None, True\n"
)
# Verbatim upstream `_goal_judge_available` tail — the completion gate's
# reachability probe, whose two False paths logged nothing.
PINNED_JUDGE_AVAILABLE_SOURCE = '''\
def _goal_judge_available() -> bool:
    try:
        from agent.auxiliary_client import get_text_auxiliary_client
        client, model = get_text_auxiliary_client("goal_judge")
    except Exception:
        return False
    return client is not None and bool(model)
'''
JUDGE_ERROR = ("continue", "judge error: NotFoundError", False, None, True)
# The two anchor regions of upstream judge_goal, verbatim and in order, with
# the lines between them dropped — no patch keys on those, and the except
# handler is already pinned above. Identical in v2026.8.3 and v2026.8.13.
PINNED_JUDGE_CALL_SOURCE = '''\
    try:
        resp = call_llm(
            task="goal_judge",
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=_goal_judge_max_tokens(),
            timeout=timeout,
        )
    except Exception:
        raw = ""

    verdict, reason, parse_failed, wait_directive = _parse_judge_response(raw)
'''
