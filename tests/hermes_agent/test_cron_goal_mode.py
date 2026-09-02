"""Opt-in goal mode for native cron jobs.

Runs the role's own patch tasks against a pinned upstream snippet, then
executes the resulting helper against a stub agent. The behavioural half
matters more than the textual half here: the helper sits between the cron
runner and its result-unwrap contract, and the failure that would hurt is
silent — returning the goal loop's decision dict would pass the caller's
``isinstance(result, dict)`` guard and then be misread as a failed run.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import Any

import pytest
import yaml
from jinja2 import Environment

from conftest import (
    KANBAN_GOAL_FINALIZE_TEMPLATE,
    PINNED_CRON_SUBMIT_SOURCE,
    ROLE_ROOT,
    _apply_runtime_patch,
    _goal_runner_namespace,
    _StubAgent,
    _task,
    role_defaults,
)


REPLACE_TASK = "Route the cron conversation through the goal-mode runner"
BLOCK_TASK = "Patch Hermes cron scheduler with an opt-in goal-mode runner"

@pytest.fixture
def stub_goals(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand in for hermes_cli.goals.run_kanban_goal_loop, recording its inputs."""
    seen: dict[str, Any] = {"calls": 0}

    def fake_loop(*, task_id, goal_text, run_turn, task_status_fn, block_fn,
                  first_response="", log=None, max_turns=None):
        seen["calls"] += 1
        seen["task_id"] = task_id
        seen["goal_text"] = goal_text
        seen["max_turns"] = max_turns
        seen["first_response"] = first_response
        # A cron run owns no card, so the adapter SYNTHESIZES a status. The
        # real loop treats anything outside ("running", "ready") as terminal
        # and returns before judging, so this must be one of those two — the
        # shipped adapter returned None here and the judge never ran once.
        assert task_status_fn() in ("running", "ready")
        run_turn("continue 1")
        run_turn("continue 2")
        block_fn("budget spent")
        return {"outcome": "blocked_budget", "turns_used": 2, "reason": "x"}

    goals = types.ModuleType("hermes_cli.goals")
    goals.run_kanban_goal_loop = fake_loop
    goals.KANBAN_GOAL_FINALIZE_TEMPLATE = KANBAN_GOAL_FINALIZE_TEMPLATE
    package = types.ModuleType("hermes_cli")
    package.goals = goals
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.goals", goals)
    return seen


def test_submit_patch_applies_once_to_pinned_source() -> None:
    patched = _apply_runtime_patch(REPLACE_TASK, PINNED_CRON_SUBMIT_SOURCE)
    assert "_hermes_cron_goal_run, agent, prompt, job_name)" in patched
    # The upstream call must be gone, not merely shadowed: a `replace` that
    # matched nothing still reports ok on the guest.
    assert "agent.run_conversation, prompt)" not in patched


def test_patched_submit_keeps_its_block_indentation() -> None:
    """Indentation is what a multi-line `replace` gets wrong.

    The statement lives inside a `try:` inside a function; losing its indent
    yields source that compiles nowhere and fails only on the guest.
    """
    patched = _apply_runtime_patch(REPLACE_TASK, PINNED_CRON_SUBMIT_SOURCE)
    body = [ln for ln in patched.splitlines() if "_cron_future" in ln]
    assert body, patched
    assert all(ln.startswith("        ") for ln in body), body
    # Compiles as a standalone block once the shared indent is stripped.
    compile(
        "\n".join(ln[8:] for ln in patched.splitlines()),
        "patched-submit",
        "exec",
    )


def test_env_unset_leaves_cron_behaviour_untouched(
    monkeypatch: pytest.MonkeyPatch, stub_goals: dict[str, Any]
) -> None:
    """The safety property: with the feature off, nothing about a cron run changes."""
    monkeypatch.delenv("HERMES_CRON_GOAL_JOBS", raising=False)
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    agent = _StubAgent()
    result = run(agent, "do the thing", "review")
    assert agent.turns == 1
    assert stub_goals["calls"] == 0
    assert result == {
        "final_response": "resp1",
        "messages": ["m1"],
        "completed": True,
        "failed": False,
    }


def test_every_cron_conversation_is_tagged_with_its_job_name(
    monkeypatch: pytest.MonkeyPatch, stub_goals: dict[str, Any]
) -> None:
    """Without this, a failed cron run cannot be found in the agent log at all.

    ``AIAgent.run_conversation`` does ``effective_task_id = task_id or
    str(uuid.uuid4())`` and stamps the result on every log line the run emits.
    Omit it and a cron run is indistinguishable from an API session — measured
    on the guest: 6,072 id-tagged lines in one day, every one ``api-`` prefixed,
    none joinable to a cron job. Three separate causes were proposed and
    eliminated for two dead jobs before anyone noticed the join key was missing.

    This asserts the UNCONDITIONAL path — the call above the goal-mode check,
    which every cron job takes whether or not it is goal-judged.
    """
    monkeypatch.delenv("HERMES_CRON_GOAL_JOBS", raising=False)
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    agent = _StubAgent()
    run(agent, "do the thing", "zammad-review")
    assert agent.task_ids == ["cron:zammad-review"], (
        "the cron conversation was not tagged with its job name; its log lines "
        "will carry an opaque uuid and be unattributable"
    )


def test_the_task_id_is_derived_from_the_job_not_a_constant(
    monkeypatch: pytest.MonkeyPatch, stub_goals: dict[str, Any]
) -> None:
    """A fixed string would tag every job identically and defeat the purpose.

    Distinguishes the shipped design from one that hardcodes a single marker:
    two different jobs must produce two different ids.
    """
    monkeypatch.delenv("HERMES_CRON_GOAL_JOBS", raising=False)
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    first, second = _StubAgent(), _StubAgent()
    run(first, "p", "splunk-security")
    run(second, "p", "github-triage")
    assert first.task_ids == ["cron:splunk-security"]
    assert second.task_ids == ["cron:github-triage"]
    assert first.task_ids != second.task_ids


def test_the_task_id_literal_is_derived_in_exactly_one_place() -> None:
    """DRY: the goal loop and the conversation calls must not drift apart.

    The prefix previously existed as a bare literal at the goal-loop call. With
    three consumers, a limit that exists twice will disagree — so the string is
    built by one helper and every caller routes through it.
    """
    source = (
        ROLE_ROOT / "tasks" / "patches_cron_goal_mode.yml"
    ).read_text()
    body = "\n".join(
        ln for ln in source.splitlines() if not ln.lstrip().startswith("#")
    )
    assert body.count('"cron:"') == 1, (
        'the "cron:" prefix must be built in exactly one place '
        "(_cron_task_id); found it repeated"
    )
    # The kwarg form, which appears at call sites and not at the definition —
    # counting the bare name would also match `def _cron_task_id(job_name):`.
    assert body.count("task_id=_cron_task_id(job_name)") == 3, (
        "expected all three call sites (two conversations + the goal loop) "
        "to route through the helper"
    )
    assert body.count("def _cron_task_id(") == 1


def test_job_outside_the_allowlist_is_untouched(
    monkeypatch: pytest.MonkeyPatch, stub_goals: dict[str, Any]
) -> None:
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "review")
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    agent = _StubAgent()
    run(agent, "p", "github-triage")
    assert agent.turns == 1
    assert stub_goals["calls"] == 0


def test_listed_job_is_rejudged_with_history_threaded(
    monkeypatch: pytest.MonkeyPatch, stub_goals: dict[str, Any]
) -> None:
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "review")
    monkeypatch.setenv("HERMES_CRON_GOAL_MAX_TURNS", "8")
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    agent = _StubAgent()
    run(agent, "achieve X", "review")

    assert stub_goals["calls"] == 1
    assert agent.turns == 3, "first turn plus the judge's two continuations"
    assert stub_goals["max_turns"] == 8
    assert stub_goals["task_id"] == "cron:review"
    assert stub_goals["goal_text"] == "achieve X"
    assert stub_goals["first_response"] == "resp1"
    # The judge's feedback is worthless without the history it is judging, and
    # a cron agent holds none between calls — it must travel explicitly.
    assert agent.histories == [None, ["m1"], ["m2"]]


def test_returns_conversation_dict_never_the_decision_dict(
    monkeypatch: pytest.MonkeyPatch, stub_goals: dict[str, Any]
) -> None:
    """The silent failure this helper exists to avoid.

    The goal loop returns {"outcome", "turns_used", "reason"}. That is a dict,
    so it would sail through the caller's isinstance guard, then read as
    completed-is-missing and deliver an empty cron result.
    """
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "review")
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    result = run(_StubAgent(), "p", "review")
    assert result["final_response"] == "resp3"
    assert "outcome" not in result
    assert result["completed"] is True


def test_malformed_turn_budget_falls_back_to_the_loop_default(
    monkeypatch: pytest.MonkeyPatch, stub_goals: dict[str, Any]
) -> None:
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "review")
    monkeypatch.setenv("HERMES_CRON_GOAL_MAX_TURNS", "not-a-number")
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    run(_StubAgent(), "p", "review")
    assert stub_goals["max_turns"] is None


def test_missing_goals_module_degrades_to_a_single_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HERMES_CRON_GOAL_JOBS", "review")
    monkeypatch.setitem(sys.modules, "hermes_cli.goals", None)
    run = _goal_runner_namespace()["_hermes_cron_goal_run"]
    agent = _StubAgent()
    result = run(agent, "p", "review")
    assert agent.turns == 1
    assert result["final_response"] == "resp1"


def test_allowlist_default_is_exactly_the_splunk_triage_job() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_cron_goal_mode_jobs"] == ["splunk-triage"]
    # Every added name multiplies that job's serving occupancy by up to the
    # turn budget, against a tier that admits one request at a time. Widening
    # this is a deliberate capacity decision, not a config tweak.


def test_gateway_unit_exports_the_allowlist_and_budget() -> None:
    template = (ROLE_ROOT / "templates" / "hermes-gateway.service.j2").read_text()
    env = Environment(autoescape=False)  # noqa: S701
    # `comment` is an ansible filter, absent from bare Jinja2. Stubbed rather
    # than stripped from the template, so the unit renders as it really is.
    env.filters["comment"] = lambda text, *a, **kw: "\n".join(
        f"# {line}" for line in str(text).splitlines()
    )
    rendered = env.from_string(template).render(
        hermes_agent_user="hermes",
        hermes_agent_hermes_home="/home/hermes/.hermes",
        hermes_agent_gateway_cmd="/usr/bin/true",
        hermes_agent_cron_goal_mode_jobs=["splunk-triage"],
        hermes_agent_kanban_goal_max_turns=8,
    )
    assert "Environment=HERMES_CRON_GOAL_JOBS=splunk-triage" in rendered
    assert "Environment=HERMES_CRON_GOAL_MAX_TURNS=8" in rendered


def test_no_per_job_goal_budget_was_introduced() -> None:
    """assert_brain_and_bridge.yml forbids per-item turn budgets; keep it true.

    The intuitive next change here is "let the slow job have more turns". The
    loop re-sends the whole conversation every turn, so a bigger budget costs
    prefill quadratically and is only ever spent by a job already failing.
    """
    cron_defaults = yaml.safe_load(
        (ROLE_ROOT / "defaults" / "main" / "43-direct-cron-jobs-core.yml").read_text()
    )
    for jobs in cron_defaults.values():
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if isinstance(job, dict):
                assert "goal_max_turns" not in job
                assert "goal_mode" not in job
