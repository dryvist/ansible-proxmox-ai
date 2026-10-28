"""The kanban goal loop, VERBATIM from the pinned upstream release.

Its own file because it is large and because it must stay byte-exact: this is
the source two role patches anchor into AND the control flow the cron
goal-mode adapter has to satisfy.

NOT a paraphrase, and never again a reduction. The previous copy here was a
hand-reduced skeleton that dropped the `status == "blocked"` branch and fed
`run_turn` a literal `"continue"` instead of the real finalize template. Both
omissions were invisible to every test, and they hid a real defect: the cron
adapter passed a `task_status_fn` returning None, which this function treats
as archived and returns `stopped` on — so `judge_goal` was never reached and
the auxiliary judge produced no traffic at all. A reduction cannot prove an
adapter satisfies a contract it does not fully reproduce.

Refresh with scripts/verify-pinned-patches.py when hermes_agent_version moves.
"""

from __future__ import annotations

import sys
import types


def _stub_portal_tags() -> None:
    """The verbatim loop imports agent.portal_tags when it runs, and the test
    tree has no upstream `agent` package. Affinity scoping does nothing for
    these tests, so bind inert stand-ins under that module name."""
    if "agent.portal_tags" in sys.modules:
        return
    portal_tags = types.ModuleType("agent.portal_tags")
    portal_tags.get_affinity_scope = lambda: None
    portal_tags.set_affinity_scope = lambda scope: None
    portal_tags.reset_affinity_scope = lambda token: None
    package = sys.modules.get("agent") or types.ModuleType("agent")
    package.portal_tags = portal_tags
    sys.modules["agent"] = package
    sys.modules["agent.portal_tags"] = portal_tags


_stub_portal_tags()

PINNED_KANBAN_GOAL_LOOP_SOURCE = '''\
# Worker-driven terminal task statuses → loop outcome. The card's own acceptance criteria are the
# goal; the worker already has the full task body, so these outcomes stop the loop cleanly.
_KANBAN_TERMINAL_STATUSES = {
    "done": ("completed_by_worker", "worker completed the task", "task {task_id} completed by worker after {turns} turn(s)"),
    "blocked": ("blocked_by_worker", "worker blocked the task", "task {task_id} blocked by worker after {turns} turn(s)"),
    # kanban_request_review is a legitimate terminator: implementation done, awaiting a reviewer.
    "review": ("review_requested_by_worker", "worker requested review", "task {task_id} handed off for review by worker after {turns} turn(s)"),
    "changes_requested": ("changes_requested_by_reviewer", "reviewer requested changes", "reviewer returned task {task_id} for changes after {turns} turn(s)"),
}


def run_kanban_goal_loop(
    *,
    task_id: str,
    goal_text: str,
    run_turn,
    task_status_fn,
    block_fn,
    max_turns: int = DEFAULT_MAX_TURNS,
    first_response: str = "",
    log=None,
) -> dict[str, Any]:
    """Drive a kanban worker through a Ralph-style goal loop.

    Each iteration: stop if the worker already terminated the task (``kanban_complete`` /
    ``kanban_block`` / review hand-off); otherwise judge the latest response against ``goal_text``
    (the card's title + body) and feed a continuation or finalize nudge. A WAIT verdict is treated
    as CONTINUE (workers finish via kanban tools, not by parking).
    """

    def _log(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    def _block(message: str) -> None:
        try:
            block_fn(message)
        except Exception as exc:
            _log(f"kanban goal loop: block_fn failed ({exc})")

    def _result(outcome: str, reason: str) -> dict[str, Any]:
        return {"outcome": outcome, "turns_used": turns_used, "reason": reason}

    max_turns = int(max_turns or DEFAULT_MAX_TURNS)
    if max_turns < 1:
        max_turns = DEFAULT_MAX_TURNS

    last_response = first_response or ""
    turns_used = 1   # the first turn already consumed one unit of budget
    nudged_to_finalize = False

    while True:
        try:
            status = task_status_fn()
        except Exception as exc:
            _log(f"kanban goal loop: status check failed ({exc}); stopping")
            return _result("stopped", "status check failed")

        terminal = _KANBAN_TERMINAL_STATUSES.get(status)
        if terminal is not None:
            outcome, reason, log_fmt = terminal
            _log("kanban goal loop: " + log_fmt.format(task_id=task_id, turns=turns_used))
            return _result(outcome, reason)
        if status not in ("running", "ready"):
            # Reclaimed / archived / unexpected — let the dispatcher own it.
            _log(f"kanban goal loop: task {task_id} status={status!r}; stopping")
            return _result("stopped", f"status={status}")

        from agent.portal_tags import get_affinity_scope, reset_affinity_scope, set_affinity_scope
        affinity_token = None if get_affinity_scope() else set_affinity_scope(f"kanban:{task_id}")
        try:
            verdict, reason, _parse_failed, _wait, _transport_failed = judge_goal(goal_text, last_response)
        finally:
            if affinity_token is not None:
                reset_affinity_scope(affinity_token)
        if _transport_failed:
            _log(f"kanban goal loop: judge transport failed on turn {turns_used}; stopping")
            return _result("stopped", "judge transport failure")
        if verdict == "wait":
            verdict = "continue"
        _log(f"kanban goal loop: turn {turns_used}/{max_turns} verdict={verdict} reason={_truncate(reason, 120)}")

        if verdict == "blocked":
            # Unachievable is NOT done: block the card with the judge's reason now instead of
            # re-poking an impossible goal, and never let it land in done.
            # The judge ruled the goal cannot be satisfied at all — this is NOT done (#100954).
            _log(f"kanban goal loop: task {task_id} judged unachievable; blocking")
            _block(f"Goal-mode judge ruled the goal unachievable: {reason}")
            return _result("blocked_unachievable", f"judge verdict blocked: {reason}")

        if verdict == "done":
            if nudged_to_finalize:
                # Already asked once to call kanban_complete — block for review rather than spin.
                _log(f"kanban goal loop: task {task_id} judged done but worker won't finalize; blocking")
                _block(
                    f"Goal-mode worker's output looked complete but it never "
                    f"called kanban_complete after a finalize nudge ({reason})."
                )
                return _result("blocked_budget", "judged done, never finalized")
            prompt = KANBAN_GOAL_FINALIZE_TEMPLATE.format(reason=_truncate(reason, 400))
            nudged_to_finalize = True
        else:
            prompt = KANBAN_GOAL_CONTINUATION_TEMPLATE.format(reason=_truncate(reason, 400))

        # Budget check BEFORE spending another turn.
        if turns_used >= max_turns:
            _log(f"kanban goal loop: task {task_id} exhausted {turns_used}/{max_turns} turns; blocking")
            _block(
                f"Goal-mode worker exhausted its turn budget "
                f"({turns_used}/{max_turns}) without completing the task. "
                f"Last judge verdict: {_truncate(reason, 300)}"
            )
            return _result("blocked_budget", "turn budget exhausted")

        try:
            result = run_turn(prompt)
            if isinstance(result, dict):
                last_response = result.get("response", "") or ""
                failed = bool(result.get("failed", False))
                failure_reason = result.get("failure_reason") or "unknown"
            else:
                # backward compatibility: assume it's a string
                last_response = result or ""
                failed = False
                failure_reason = None
        except Exception as exc:
            _log(f"kanban goal loop: run_turn failed ({exc}); stopping")
            return _result("stopped", f"run_turn error: {type(exc).__name__}")
        if failed:
            _log(f"kanban goal loop: worker failed on turn {turns_used} (reason={failure_reason}); stopping")
            return _result("stopped", f"worker failed: {failure_reason}")
        turns_used += 1
'''


# The loop's two continuation prompts, VERBATIM upstream. The finalize one is
# load-bearing beyond its text: it is the ONLY signal an adapter gets that the
# judge said "done", so the cron adapter matches on its literal prefix.
KANBAN_GOAL_CONTINUATION_TEMPLATE = (
    "[Continuing toward this kanban task — judge says it is not done yet]\n"
    "Reason: {reason}\n\n"
    "Take the next concrete step toward completing the task. When the work "
    "is genuinely finished, call kanban_complete with a summary. If you are "
    "blocked and need human input, call kanban_block with a reason. Do not "
    "stop without calling one of them."
)
KANBAN_GOAL_FINALIZE_TEMPLATE = (
    "[The work looks complete, but the task is still open]\n"
    "Reason: {reason}\n\n"
    "If the task is genuinely done, call kanban_complete now with a short "
    "summary of what you did. If something still blocks completion, call "
    "kanban_block with the reason instead."
)
