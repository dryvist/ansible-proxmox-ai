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
    # A failed run must reach the issues channel, not the work surface.
    assert "_deliver_result(_routed_job, deliver_content," in conditions
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


def test_cron_cli_exit_code_conditions_reject_unpatched_source() -> None:
    """The cron exit-code assertion must fail against upstream's own source.

    Upstream's ``cmd_cron`` calls ``cron_command(args)`` and discards the
    return value, so a failed ``hermes cron`` action exits 0. Measured on the
    live guest 2026-08-16: ``hermes cron run <missing>`` prints "Failed to run
    job: ... not found" and still returns 0. It is loud to a human and silent
    to a program, which is why the brain watchdog re-reads job state off
    ``cron list --all`` rather than trusting ``$?``.

    Asserting the patched form is present proves nothing on its own — a
    condition that also holds for unpatched source would let the patch stop
    applying unnoticed. This pins that it does not hold.
    """
    from jinja2 import Environment

    # Retired as a role patch: upstream's own cmd_cron now forwards its
    # return value natively via _forward_command(..., forward_return=True),
    # so this is tracked in the "upstream still supplies" task, not the
    # required-patch assert. PATCHED_CLI_MAIN_SOURCE/PINNED_CLI_MAIN_SOURCE
    # (conftest.py) represent the OLD patch-based `return cron_command(args)`
    # shape and do not apply to this upstream-native check.
    NATIVE_FORWARD_SOURCE = (
        'cmd_cron = _forward_command("cmd_cron", "hermes_cli.cron", '
        '"cron_command", forward_return=True,\n)\n'
    )
    OLD_UNFORWARDED_SOURCE = (
        "def cmd_cron(args):\n"
        '    """Cron job management."""\n'
        "    from hermes_cli.cron import cron_command\n"
        "\n"
        "    cron_command(args)\n"
    )

    conditions = [
        c
        for c in _task(
            "Assert upstream still supplies the behavior these retired patches used to add"
        )["ansible.builtin.assert"]["that"]
        if "hermes_agent_cli_main_source" in c
    ]
    assert conditions, "no assertion covers the cron CLI exit code"

    env = Environment(autoescape=False)

    def _holds(source: str) -> bool:
        return all(
            bool(env.compile_expression(c)(hermes_agent_cli_main_source=source))
            for c in conditions
        )

    assert _holds(NATIVE_FORWARD_SOURCE)
    assert not _holds(OLD_UNFORWARDED_SOURCE), (
        "the cron exit-code conditions hold against upstream's unpatched "
        "cmd_cron, so the patch could silently stop applying"
    )
