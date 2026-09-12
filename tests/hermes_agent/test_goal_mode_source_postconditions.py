from __future__ import annotations

from conftest import (
    PATCHED_JUDGE_AVAILABLE_SOURCE,
    PATCHED_KANBAN_GOAL_LOOP_SOURCE,
    PATCHED_TURN_ITERATION_PREP_SOURCE,
    PINNED_JUDGE_AVAILABLE_SOURCE,
    PINNED_BOOST_CAP_SOURCE,
    PINNED_COMPRESSOR_SCAN_SOURCE,
    PINNED_CREATE_TASK_SOURCE,
    PINNED_CRON_DELIVERY_SOURCE,
    PINNED_GOAL_COMPLETION_SOURCE,
    PINNED_HINDSIGHT_PREFETCH_SOURCE,
    PATCHED_JUDGE_CALL_SOURCE,
    PINNED_JUDGE_CALL_SOURCE,
    PINNED_JUDGE_ERROR_SENTINEL_SOURCE,
    PINNED_KANBAN_GOAL_LOOP_SOURCE,
    PINNED_PROTOCOL_RETRY_SOURCE,
    PINNED_PROTOCOL_VIOLATION_SOURCE,
    PINNED_STALE_RECLAIM_TERMINATE_SOURCE,
    PINNED_TC_BOOST_CAP_SOURCE,
    PINNED_WORKER_REAP_SOURCE,
    PINNED_WORKER_SPAWN_SOURCE,
    _apply_runtime_patch,
    _combined_assert_task,
    _source_postconditions,
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

    # Upstream-supplied now, not patch output: the arity, message, and
    # failure_limit patches were retired for matching zero times.
    completion_source = PINNED_GOAL_COMPLETION_SOURCE
    # Same module as the completion gate, so it rides the same source var.
    completion_source += PATCHED_JUDGE_AVAILABLE_SOURCE
    reconcile_source = _apply_runtime_patch(
        "Patch Hermes idempotent create to reconcile goal-mode fields",
        PINNED_CREATE_TASK_SOURCE,
    )
    # The worker-spawn "--quiet" patch that used to apply here is retired
    # (2026-09): upstream supplies the goal-mode quiet CLI path natively, so
    # this snippet rides along unpatched — no assertion in the combined
    # required-patch task targets its content any more.
    reconcile_source = PINNED_WORKER_SPAWN_SOURCE + reconcile_source
    # Upstream's September 2026 decomposition moved worker-reap
    # process-group guarding AND both protocol-violation retirements out of
    # kanban_db.py (hermes_agent_goal_reconcile_source) into
    # kanban_db_dispatch.py, checked instead against its own dedicated
    # hermes_agent_kanban_dispatch_source — see
    # conftest.PATCHED_KANBAN_DISPATCH_SOURCE, built the same way (real
    # patch tasks over minimal real fragments), reused here rather than
    # rebuilt so this file and conftest can't drift apart on the same
    # fixture.
    worker_reap_source = _apply_runtime_patch(
        "Patch Hermes worker-reap SIGKILL escalation to signal the worker's process group",
        _apply_runtime_patch(
            "Patch Hermes worker-reap timeout path to signal the worker's process group",
            _apply_runtime_patch(
                "Patch Hermes worker-reap timeout path to verify PID safety before signaling",
                PINNED_WORKER_REAP_SOURCE,
            ),
        ),
    )
    stale_reclaim_source = _apply_runtime_patch(
        "Patch Hermes worker-reap SIGKILL escalation to signal the worker's process group",
        _apply_runtime_patch(
            "Patch Hermes stale-reclaim worker termination to signal the worker's process group",
            _apply_runtime_patch(
                "Patch Hermes stale-reclaim worker termination to verify PID safety before signaling",
                PINNED_STALE_RECLAIM_TERMINATE_SOURCE,
            ),
        ),
    )
    kanban_dispatch_source = (
        _task("Patch Hermes worker-reap helper to verify PID identity before signaling")[
            "ansible.builtin.blockinfile"
        ]["block"]
        + worker_reap_source
        + stale_reclaim_source
        + PINNED_PROTOCOL_VIOLATION_SOURCE
        + PINNED_PROTOCOL_RETRY_SOURCE
    )
    # The six client-side backoff hacks are gone (see
    # test_client_side_backoff_hacks_stay_reverted); only the two kept
    # max_tokens-ceiling patches still contribute to this source.
    # Both kanban_db.py creation paths carry the same anchor line, and the
    # assignee guard is asserted by COUNT — so the fixture needs both, or a
    # patch that only reached one would still pass here.
    for _tail in ("    if not title or not title.strip():\n",
                  "    with write_txn(conn):\n"):
        reconcile_source += _apply_runtime_patch(
            "Reject Kanban cards created for an assignee with no profile",
            "    assignee = _canonical_assignee(assignee)\n" + _tail,
        )

    # Both max_tokens-ceiling patches moved off retry_source (now unused by
    # any condition) onto their own dedicated vars — see
    # conftest.PATCHED_TURN_ITERATION_PREP_SOURCE and
    # conftest.PATCHED_TURN_TRUNCATION_SOURCE, reused here via
    # _source_postconditions' defaults.
    retry_source = ""
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
    # Re-anchored (PR I): both protocol-violation checks moved off
    # reconcile_source onto kanban_dispatch_source with the rest of the
    # September 2026 kanban_db_dispatch.py decomposition.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            kanban_dispatch_source=kanban_dispatch_source.replace(
                "without a terminal kanban call counts as failed no",
                "",
            ),
        )
    )
    # The forced first-failure give-up back in upstream: a protocol violation
    # would again retire the card on its first occurrence, so the postconditions
    # must go red even though the message is intact.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            kanban_dispatch_source=kanban_dispatch_source.replace(
                "failure_limit=1 if is_systemic else None,",
                "failure_limit=1 if (protocol_violation or is_systemic) else None,",
            ),
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
    # The judge latency emission dropped (upstream call site unpatched): judge
    # timing would be missing from the index the fabric is measured in, so the
    # converge must go red rather than pass quietly.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            goal_judge_source=(
                "DEFAULT_JUDGE_TIMEOUT = 60.0\n"
                + PINNED_JUDGE_ERROR_SENTINEL_SOURCE
                + PATCHED_KANBAN_GOAL_LOOP_SOURCE
                + PINNED_JUDGE_CALL_SOURCE
            ),
        )
    )
    # A double insertion is equally wrong: the count assertion must reject it.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            goal_judge_source=(
                "DEFAULT_JUDGE_TIMEOUT = 60.0\n"
                + PINNED_JUDGE_ERROR_SENTINEL_SOURCE
                + PATCHED_KANBAN_GOAL_LOOP_SOURCE
                + PATCHED_JUDGE_CALL_SOURCE * 2
            ),
        )
    )
    # The availability probe left unpatched: a goal-mode card can complete
    # with the completion gate silently skipped, which is exactly the state
    # that leaves no trace anywhere. Must go red.
    assert not all(
        _source_postconditions(
            PINNED_GOAL_COMPLETION_SOURCE + PINNED_JUDGE_AVAILABLE_SOURCE,
            reconcile_source,
            retry_source,
            auxiliary_source,
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
    # The length-continuation boost (turn_iteration_prep_source, its own
    # dedicated var since PR C moved this patch's target file) dropped: the
    # needle `_boost_cap = agent.max_tokens ...` reverting to the unpatched
    # `_boost_cap = max(...)` form must go red on its own, independent of
    # the unrelated _tc_boost_cap patch (now on its own turn_truncation_
    # source var, PR I — see the default PATCHED_TURN_TRUNCATION_SOURCE).
    unpatched_turn_iteration_prep_source = PATCHED_TURN_ITERATION_PREP_SOURCE.replace(
        "_boost_cap = agent.max_tokens if agent.max_tokens else max("
        "32768, _requested_cap or 0)",
        "_boost_cap = max(32768, _requested_cap or 0)",
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            turn_iteration_prep_source=unpatched_turn_iteration_prep_source,
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
    # Worker-reap process-group guard dropped: kanban_dispatch_source
    # reverts to not carrying the patched reap at all — must go red.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            kanban_dispatch_source=kanban_dispatch_source.replace(worker_reap_source, ""),
        )
    )
    # Stale-reclaim process-group guard dropped: kanban_dispatch_source
    # reverts to not carrying the patched reclaim termination at all —
    # must go red.
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            kanban_dispatch_source=kanban_dispatch_source.replace(stale_reclaim_source, ""),
        )
    )


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
