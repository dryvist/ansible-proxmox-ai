"""The cron goal-mode adapter, driven through the REAL upstream loop.

Split from test_cron_goal_mode.py, which covers the patch TEXT (does the
regexp apply, does indentation survive). This file covers whether the adapter
actually satisfies the loop's contract — a different question, and the one
that went unasked while the shipped adapter never invoked the judge at all.

A stubbed loop cannot answer it: the stub decides for itself whether to keep
going, so it reports success against a `task_status_fn` the real loop treats
as terminal.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from conftest import (
    KANBAN_GOAL_CONTINUATION_TEMPLATE,
    KANBAN_GOAL_FINALIZE_TEMPLATE,
    PINNED_KANBAN_GOAL_LOOP_SOURCE,
    ROLE_ROOT,
    _goal_runner_namespace,
    _StubAgent,
    role_tasks,
)


def _real_loop(verdicts):
    """Exec the verbatim upstream goal loop with a scripted judge.

    The whole point: a stub of this function cannot prove the adapter drives
    it correctly. The shipped adapter passed `task_status_fn=lambda: None`,
    which this loop treats as archived — it returned `stopped` on the first
    iteration and `judge_goal` was never called, while a stubbed loop that
    ignored the status happily reported success.
    """
    seq = list(verdicts)
    calls = {"judge": 0}

    def judge_goal(goal_text, last_response):
        calls["judge"] += 1
        verdict = seq.pop(0) if len(seq) > 1 else seq[0]
        return (verdict, "because", False, None, False)

    namespace: dict[str, Any] = {
        "DEFAULT_MAX_TURNS": 8,
        "judge_goal": judge_goal,
        "_truncate": lambda text, n: str(text)[:n],
        "KANBAN_GOAL_CONTINUATION_TEMPLATE": KANBAN_GOAL_CONTINUATION_TEMPLATE,
        "KANBAN_GOAL_FINALIZE_TEMPLATE": KANBAN_GOAL_FINALIZE_TEMPLATE,
        "Dict": dict,
        "Any": object,
    }
    exec(  # noqa: S102
        compile(PINNED_KANBAN_GOAL_LOOP_SOURCE, "pinned-goal-loop", "exec"),
        namespace,
    )
    return namespace["run_kanban_goal_loop"], calls


@pytest.fixture
def real_goals(monkeypatch: pytest.MonkeyPatch):
    """Install the VERBATIM upstream loop as hermes_cli.goals."""

    def install(verdicts):
        loop, calls = _real_loop(verdicts)
        goals = types.ModuleType("hermes_cli.goals")
        goals.run_kanban_goal_loop = loop
        goals.KANBAN_GOAL_FINALIZE_TEMPLATE = KANBAN_GOAL_FINALIZE_TEMPLATE
        package = types.ModuleType("hermes_cli")
        package.goals = goals
        monkeypatch.setitem(sys.modules, "hermes_cli", package)
        monkeypatch.setitem(sys.modules, "hermes_cli.goals", goals)
        return calls

    return install



def test_judge_actually_runs_against_the_real_loop(
    monkeypatch: pytest.MonkeyPatch, real_goals
) -> None:
    """The regression that shipped: judge_goal was never reached.

    `task_status_fn=lambda: None` hits the real loop's
    `if status not in ("running", "ready")` branch, which returns `stopped`
    BEFORE judging. Every mocked test passed while the auxiliary judge sat
    idle in production. Nothing but the real loop catches this.
    """
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "splunk-triage")
    calls = real_goals(["continue", "continue", "done"])
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    agent = _StubAgent()

    run(agent, "sweep splunk", "splunk-triage")

    assert calls["judge"] > 0, "the judge never ran — the loop exited early"
    assert agent.turns > 1, "no continuation turn — the judge drove nothing"


def test_satisfied_judge_completes_without_a_wasted_turn(
    monkeypatch: pytest.MonkeyPatch, real_goals
) -> None:
    """A satisfied judge must not be labelled a budget failure.

    The loop answers `verdict == "done"` by nudging the worker to call
    kanban_complete. A cron run owns no card, so answering that nudge burns a
    full worker conversation on an instruction about an object that does not
    exist, and the loop then reports `blocked_budget` — the same outcome a
    genuine failure produces. Longitudinal eval of judge quality is worthless
    if success and failure share an outcome label.
    """
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "splunk-triage")
    real_goals(["done"])
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    agent = _StubAgent()

    run(agent, "sweep splunk", "splunk-triage")

    # One conversation: the original run. The finalize nudge is intercepted.
    assert agent.turns == 1


def test_turn_budget_floor_forbids_the_degenerate_single_turn() -> None:
    """max_turns=1 makes a satisfied judge indistinguishable from a failure.

    The loop's budget check runs BEFORE run_turn, so at 1 it returns
    blocked_budget on every run whatever the verdict, and the adapter's
    finalize interception is unreachable. Verified against the real loop:
    budget=1 with a "done" verdict yields blocked_budget plus a warning
    saying the judge never agreed. The role must refuse to configure it.
    """
    conditions = " ".join(
        str(condition)
        for task in role_tasks(ROLE_ROOT)
        if task.get("name") == "Assert Hermes recurring goal-mode settings are valid"
        for condition in task["ansible.builtin.assert"]["that"]
    )
    assert "hermes_agent_kanban_goal_max_turns | int >= 2" in conditions
    assert "int >= 1" not in conditions, "the >= 1 floor permits the degenerate case"


def test_real_loop_still_returns_the_conversation_dict(
    monkeypatch: pytest.MonkeyPatch, real_goals
) -> None:
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "splunk-triage")
    real_goals(["continue", "done"])
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]

    result = run(_StubAgent(), "sweep splunk", "splunk-triage")

    assert "outcome" not in result, "returned the decision dict, not the run"
    assert result["completed"] is True
