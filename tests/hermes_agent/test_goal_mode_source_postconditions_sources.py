from __future__ import annotations

from conftest import (
    PATCHED_JUDGE_AVAILABLE_SOURCE,
    PATCHED_KANBAN_DISPATCH_SOURCE,
    PATCHED_KANBAN_GOAL_LOOP_SOURCE,
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


def test_installed_source_postconditions_hold_only_for_the_patched_sources() -> None:
    """Split out of test_installed_source_postconditions_fail_closed
    (token budget): builds every source var _source_postconditions() reads
    and proves the combined assert task holds against the fully-patched
    form and goes red the moment any one patch is missing or half-applied.
    The structural/substring checks on the assert task's own `that` list
    live in test_goal_mode_source_postconditions.py, sharing nothing but
    the imports above.
    """
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
    reconcile_source = (
        PINNED_WORKER_SPAWN_SOURCE
        + reconcile_source
        + PINNED_PROTOCOL_VIOLATION_SOURCE
        + PINNED_PROTOCOL_RETRY_SOURCE
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

    # The stale-reclaim SIGKILL-escalation patch that used to sit here is
    # GONE (patches_worker_reap.yml): folded into a shared-helper rewrite
    # tracked separately, not yet applied.
    stale_reclaim_source = PINNED_STALE_RECLAIM_TERMINATE_SOURCE
    for patch_name in (
        "Patch Hermes stale-reclaim worker termination to verify PID safety before signaling",
        "Patch Hermes stale-reclaim worker termination to signal the worker's process group",
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
    # Both kanban_db.py creation paths carry the same anchor line, and the
    # assignee guard is asserted by COUNT — so the fixture needs both, or a
    # patch that only reached one would still pass here.
    for _tail in ("    if not title or not title.strip():\n",
                  "    with write_txn(conn):\n"):
        reconcile_source += _apply_runtime_patch(
            "Reject Kanban cards created for an assignee with no profile",
            "    assignee = _canonical_assignee(assignee)\n" + _tail,
        )

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
                "without a terminal kanban call counts as failed no",
                "",
            ),
            retry_source,
            auxiliary_source,
        )
    )
    # The forced first-failure give-up back in upstream: a protocol violation
    # would again retire the card on its first occurrence, so the postconditions
    # must go red even though the message is intact.
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
    # The length-continuation boost dropped while the _tc_ one landed. Since
    # the September 2026 re-anchor (PR C) each patch lives in its own file
    # (retry_source / turn_iteration_prep_source), so a half-application is
    # simulated by un-patching turn_iteration_prep_source alone — retry_source
    # stays fully patched and would satisfy every retry_source-only check even
    # with the length-continuation boost missing.
    turn_iteration_prep_source_unpatched = PINNED_BOOST_CAP_SOURCE
    assert "_tc_boost_cap = agent.max_tokens" in retry_source
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            turn_iteration_prep_source=turn_iteration_prep_source_unpatched,
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
    # Worker-reap process-group guard dropped: kanban_dispatch_source reverts
    # to not carrying the patched reap at all — must go red. Re-anchored (PR
    # C): this content moved off reconcile_source onto its own dedicated
    # kanban_dispatch_source var.
    kanban_dispatch_source = PATCHED_KANBAN_DISPATCH_SOURCE
    worker_reap_patched = _apply_runtime_patch(
        "Patch Hermes worker-reap timeout path to signal the worker's process group",
        _apply_runtime_patch(
            "Patch Hermes worker-reap timeout path to verify PID safety before signaling",
            PINNED_WORKER_REAP_SOURCE,
        ),
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            kanban_dispatch_source=kanban_dispatch_source.replace(
                worker_reap_patched, ""
            ),
        )
    )
    # Stale-reclaim process-group guard dropped: kanban_dispatch_source
    # reverts to not carrying the patched reclaim termination at all — must
    # go red.
    stale_reclaim_patched = _apply_runtime_patch(
        "Patch Hermes stale-reclaim worker termination to signal the worker's process group",
        _apply_runtime_patch(
            "Patch Hermes stale-reclaim worker termination to verify PID safety before signaling",
            PINNED_STALE_RECLAIM_TERMINATE_SOURCE,
        ),
    )
    assert not all(
        _source_postconditions(
            completion_source,
            reconcile_source,
            retry_source,
            auxiliary_source,
            kanban_dispatch_source=kanban_dispatch_source.replace(
                stale_reclaim_patched, ""
            ),
        )
    )


