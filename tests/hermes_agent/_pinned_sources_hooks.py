"""Verbatim upstream Hermes source snippets: memory-sync, goal-judge and
dispatch-tick patch sites.

Split from _pinned_sources_worker.py (over the repo's per-file token budget).
Same contract: every constant is upstream source EXACTLY as shipped, never the
expected post-patch form.
"""

from __future__ import annotations

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
        if interrupted or not (self._memory_manager and final_response and original_user_message):
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
            if not is_trivial_prompt(user_text):
                self._memory_manager.queue_prefetch_all(user_text, session_id=self.session_id or "")
        except Exception:
            pass
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
# Verbatim from hermes_cli/goals.py (v2026.9.11): the call moved into its
# own _call_goal_judge_llm() helper (returns a plain str, discards the
# response object), fetched and diffed directly against the pinned tag —
# the previous "resp = call_llm(...)" inline shape no longer exists there.
PINNED_JUDGE_CALL_SOURCE = '''\
    try:
        raw = _call_goal_judge_llm(call_llm, JUDGE_SYSTEM_PROMPT, prompt, timeout)
    except Exception as exc:
        logger.info("goal judge: API call failed (%s) — falling through to continue", exc)
        return "continue", f"judge error: {type(exc).__name__}", False, None, True

    verdict, reason, parse_failed, wait_directive = _parse_judge_response(raw)
'''


# Verbatim from gateway/kanban_watchers.py — the dispatcher's per-tick result
# loop and the stuck-streak counter the tick log patches rewrite.
PINNED_DISPATCH_TICK_SOURCE = (
    '''\
                    for slug, res in (results or []):
                        if res is not None and getattr(res, "spawned", None):
                            any_spawned = True
                            logger.info(
                                "kanban dispatcher [%s]: spawned=%d reclaimed=%d "
                                "crashed=%d timed_out=%d promoted=%d auto_blocked=%d",
                                slug,
                                len(res.spawned),
                                res.reclaimed,
                                len(res.crashed) if hasattr(res.crashed, "__len__") else 0,
                                len(res.timed_out) if hasattr(res.timed_out, "__len__") else 0,
                                res.promoted,
                                len(res.auto_blocked) if hasattr(res.auto_blocked, "__len__") else 0,
                            )
                    ready_pending = await _to_thread_process_service(_ready_nonempty)
                    bad_ticks = bad_ticks + 1 if ready_pending and not any_spawned else 0
'''
)
