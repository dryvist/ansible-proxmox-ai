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
            "ORDER BY created_at DESC LIMIT 1", (idempotency_key,),
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
# Re-anchored (PR I): upstream's September 2026 decomposition moved this to
# agent/turn_truncation.py and inlined the separate `_tc_boost_cap =`
# assignment directly into the min() call it fed.
PINNED_TC_BOOST_CAP_SOURCE = (
    "    agent._ephemeral_max_output_tokens = min(_tc_boost, "
    "max(32768, _tc_requested_cap or 0))\n"
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
# Both delivery call sites: the success path (inside the side-effect fence)
# and the outer exception handler, which delivers its own failure summary.
PINNED_CRON_DELIVERY_SOURCE = (
    '''\
                if success:
                    deliver_content = final_response
                        delivery_error = _deliver_result(
                            job,
                            deliver_content,
                            adapters=adapters,
                            loop=loop,
                        )
                    delivery_error = _deliver_result(
                        job,
                        # Composed exactly like the normal failure delivery above.
                        # mark_job_run below records THIS run in failure_streak
                        _summarize_cron_failure_for_delivery(job, _err_text)
                        + _failure_streak_nudge(job),
                        adapters=adapters,
                        loop=loop,
                    )
'''
    # Upstream's line; the memory patch deliberately leaves it in place.
    # Reversed upstream — cron now builds the built-in memory store itself.
    "            skip_memory=False,\n"
)
# Verbatim from cron/scheduler.py — the single run_conversation submit that
# opt-in cron goal mode wraps.
PINNED_CRON_SUBMIT_SOURCE = (
    "    _cron_context = contextvars.copy_context()\n"
    "    _cron_future = _cron_pool.submit(\n"
    "        _cron_context.run, agent.run_conversation, prompt, task_id=task_id)\n"
    "    _inactivity_timeout = False\n"
)
# Exact upstream v2026.9.11 monitor targeted by the aggregate-deadline patch —
# _run_agent_with_watchdog (cron/scheduler.py), from _cron_timeout's
# computation through the final `return result`. Fetched directly from
# github.com/NousResearch/hermes-agent at the pinned tag, not hand-typed:
# upstream moved inactivity detection off the poll loop onto its own daemon
# thread (_watch_inactivity/_inactivity_watchdog_loop) since v2026.8.3, and a
# hand-copied "close enough" snippet is exactly what let seven dead patches
# stay green before. Keep the complete control flow, not only replacement
# anchors: the transformed fixture is compiled and executed with fake time/
# futures/threading in the behavioral tests, so a syntactically valid but
# incorrectly composed patch cannot pass.
PINNED_CRON_TIMEOUT_SOURCE = '''\
    _cron_timeout = _cron_inactivity_seconds()
    _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
    _POLL_INTERVAL = 5.0
    _job_schedule = job.get("schedule")
    _is_oneshot = isinstance(_job_schedule, dict) and _job_schedule.get("kind") == "once"
    _run_claim = job.get("run_claim")
    _run_claim_owner = str(_run_claim.get("by") or "") if isinstance(_run_claim, dict) else ""
    _last_claim_heartbeat = time.monotonic()

    def _abort_if_fire_claim_lost() -> None:
        if cancel_event is None or not cancel_event.is_set():
            return
        if agent is not None and hasattr(agent, "interrupt"):
            agent.interrupt("Cron fire claim ownership was lost")
        raise RuntimeError(f"Cron job '{job_name}' lost its durable fire claim ownership")

    def _heartbeat_run_claim_if_due():
        nonlocal _last_claim_heartbeat
        if not _is_oneshot or not _run_claim_owner:
            return
        _mono = time.monotonic()
        if _mono - _last_claim_heartbeat < _RUN_CLAIM_HEARTBEAT_SECONDS:
            return
        _last_claim_heartbeat = _mono
        try:
            heartbeat_run_claim(job_id, expected_owner=_run_claim_owner)
        except Exception:
            logger.debug("Job '%s': run_claim heartbeat failed", job_name, exc_info=True)

    _cron_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    _cron_context = contextvars.copy_context()
    _cron_future = _cron_pool.submit(
        _cron_context.run, agent.run_conversation, prompt, task_id=task_id)
    if worker_state is not None:
        worker_state["future"] = _cron_future
    _inactivity_timeout = False
    _watch_stop = threading.Event()

    def _idle_seconds() -> float:
        if not hasattr(agent, "get_activity_summary"):
            return 0.0
        try:
            _act = agent.get_activity_summary()
            return float(_act.get("seconds_since_activity", 0.0) or 0.0)
        except Exception:
            return 0.0

    def _watch_inactivity() -> None:
        nonlocal _inactivity_timeout
        if _cron_inactivity_limit is None:
            return
        if _inactivity_watchdog_loop(
            get_idle_seconds=_idle_seconds, limit_s=_cron_inactivity_limit, poll_s=_POLL_INTERVAL,
            stop=_watch_stop, future_done=_cron_future.done):
            _inactivity_timeout = True

    _watch_thread = threading.Thread(
        target=_watch_inactivity, name=f"cron-inactivity-{str(job_id)[:8]}", daemon=True)
    try:
        if _cron_inactivity_limit is not None:
            _watch_thread.start()
        if _cron_inactivity_limit is None and not _is_oneshot and cancel_event is None:
            result = _cron_future.result()
        else:
            result = None
            while True:
                done, _ = concurrent.futures.wait({_cron_future}, timeout=_POLL_INTERVAL)
                if done:
                    _abort_if_fire_claim_lost()
                    result = _cron_future.result()
                    break
                if _inactivity_timeout:
                    break
                _abort_if_fire_claim_lost()
                _heartbeat_run_claim_if_due()
    except Exception:
        _cron_pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        _watch_stop.set()
        _cron_pool.shutdown(wait=False, cancel_futures=True)

    if _inactivity_timeout:
        _raise_inactivity_timeout(agent, job_name, _cron_inactivity_limit)

    if not isinstance(result, dict):
        raise RuntimeError(
            f"agent.run_conversation returned {type(result).__name__} instead of dict: {result!r}"
        )
    return result
'''
