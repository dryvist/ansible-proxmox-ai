from __future__ import annotations

from conftest import (
    _combined_assert_task,
    _task,
)

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
        "hermes_cli/main.py",
        "hermes_cli/kanban_db_dispatch.py",
        "agent/turn_iteration_prep.py",
        "agent/turn_truncation.py",
    ]

    assert_task = _combined_assert_task()
    conditions = " ".join(assert_task["ansible.builtin.assert"]["that"])
    assert "verdict, reason, _, _, _ = judge_goal(" in conditions
    assert any(
        "goal judge unavailable:" in condition
        and ".count(" in condition
        and ") == 2" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert "DEFAULT_JUDGE_TIMEOUT =" in conditions
    assert "SELECT id, status FROM tasks" in conditions
    assert (
        'if goal_mode and row["status"] in ("triage", "todo", "scheduled", '
        '"ready", "blocked", "review"):'
        in conditions
    )
    assert "goal_max_turns = COALESCE(?, goal_max_turns)" in conditions
    # The worker-spawn "--quiet" patch is retired (2026-09): upstream's own
    # kanban dispatcher already appends "-Q" for a goal-mode task, checked
    # against the live installed source in "Assert upstream still supplies
    # the behavior these retired patches used to add", not here.
    assert "WHERE id = ? AND status IN" in conditions
    assert "_TRANSIENT_RETRY_BACKOFF_BASE = 15.0" in conditions
    assert "status in (408, 429)" in conditions
    assert "for idx in range(end - 1, start - 1, -1):" in conditions
    assert "_cron_markup_guard(job, output_file," in conditions
    # A script-declared failure must reach the failure lane, not the runner's
    # own (possibly True) success flag. The old call-site reroute this used
    # to check ("_deliver_result(_routed_job, ...)") is retired
    # (patches_cron_failure_routing.yml) — declared_failure now threads
    # through the same for_failure lane upstream's own delivery already
    # reads.
    assert "job, deliver_content, _cron_declared_failure = _cron_route(job, deliver_content)" in conditions
    assert "for_failure=not d.success or _cron_declared_failure," in conditions
    # Output-validity guard: wraps the markup guard's call, so it must be
    # present and wired to the actual delivery-content assignment.
    assert "def _cron_output_validity_guard(job, output_file, content, success):" in conditions
    assert "deliver_content = _cron_output_validity_guard(" in conditions
    assert (
        "job, output_file, _cron_markup_guard(job, output_file, final_response"
        in conditions
    )
    assert "if _is_cron_silence_response(text):" in conditions
    assert "def _is_cron_silence_response(text: str) -> bool:" in conditions
    # The message and the retry rule are asserted as a pair: the message tells
    # the operator the card will be retried, so it must not be able to land
    # while the forced first-failure give-up is still in the source.
    assert (
        "without a terminal kanban call counts as failed no"
        in conditions
    )
    assert "failure_limit=1 if is_systemic else None," in conditions
    assert 'logger.debug("Hindsight prefetch failed:' in conditions
    assert "if _transport_failed:" in conditions
    assert "blocked_judge_unreachable" in conditions
    # The upstream sentinel producer must stay pinned: reworded upstream, the
    # guard would silently never fire.
    assert (
        'return "continue", f"judge error: {type(exc).__name__}", False, None, True'
        in conditions
    )
    assert any(
        "judge_failures = 0" in condition
        and ".count(" in condition
        and ") == 2" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    # Re-anchored (PR C): this occurrence moved off hermes_agent_retry_source
    # onto its own turn_iteration_prep_source var, and its own-file count
    # dropped from 2 to 1 as a result.
    assert any(
        "_boost_cap = agent.max_tokens if agent.max_tokens else max(" in condition
        and ".count(" in condition
        and ") == 1" in condition
        for condition in assert_task["ansible.builtin.assert"]["that"]
    )
    assert 'resolved_provider != "custom"' in conditions
    assert "update the pinned-source patches" in assert_task["ansible.builtin.assert"][
        "fail_msg"
    ]


def test_cron_cli_exit_code_condition_covers_the_retired_patch() -> None:
    """The cron exit-code behavior is upstream-native, not a role patch.

    Upstream's ``cmd_cron`` used to call ``cron_command(args)`` and discard
    the return value, so a failed ``hermes cron`` action exited 0. Measured
    on the live guest 2026-08-16: ``hermes cron run <missing>`` printed
    "Failed to run job: ... not found" and still returned 0. It is loud to a
    human and silent to a program, which is why the brain watchdog re-reads
    job state off ``cron list --all`` rather than trusting ``$?``.

    Retired as a role patch (2026-09): upstream's generated ``cmd_cron`` now
    forwards the return value itself via ``_forward_command(...,
    forward_return=True)``. There is no role-patch before/after pair left to
    unit test — the condition below is asserted against the live installed
    source in "Assert upstream still supplies the behavior these retired
    patches used to add"; this test only pins that the condition exists.
    """
    conditions = [
        c
        for c in _task(
            "Assert upstream still supplies the behavior these retired patches used to add"
        )["ansible.builtin.assert"]["that"]
        if "hermes_agent_cli_main_source" in c
    ]
    assert conditions, "no assertion covers the cron CLI exit code"
    assert any("forward_return=True" in c for c in conditions)
