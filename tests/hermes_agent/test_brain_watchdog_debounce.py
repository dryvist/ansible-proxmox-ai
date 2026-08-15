"""Safety contract for the Hermes brain watchdog: debounce, flap coalescing,
and the pause/probe readback.

Split from test_brain_watchdog_contract.py to stay under the token budget —
see _brain_watchdog_shared.py for the loaded template/defaults fixtures and
test_brain_watchdog_saturation.py for the probe-deadline, saturation, and
reconcile contracts this leaves behind.

The template already implements debounce, flap coalescing and a sustained-flap
escalation. Nothing pinned them. `test_queue_recovery_contract.py` does touch
this template, but only to assert the ALERT TEXT is honest (that it reports
command outcomes and disclaims queue health). The logic that decides whether
to pause the fleet at all — the streak debounce and the flap coalescing — had
no coverage. That gap is what this file closes; the alert-honesty assertions
are deliberately left where they already live rather than duplicated here.
"""

from _brain_watchdog_shared import DEFAULTS, WATCHDOG


def test_both_edge_thresholds_come_from_role_vars_not_literals() -> None:
    """A hardcoded threshold cannot be tuned per host and drifts from the docs."""
    assert 'DOWN_AFTER="{{ hermes_agent_brain_watchdog_down_after }}"' in WATCHDOG
    assert 'UP_AFTER="{{ hermes_agent_brain_watchdog_up_after }}"' in WATCHDOG
    assert "hermes_agent_brain_watchdog_down_after:" in DEFAULTS
    assert "hermes_agent_brain_watchdog_up_after:" in DEFAULTS


def test_pausing_the_fleet_requires_a_debounced_down_edge() -> None:
    """THE PROPERTY THAT MATTERS MOST.

    The down edge — the one that pauses every seeded cron — must be gated on a
    run of consecutive failures reaching DOWN_AFTER, never on one probe. If
    this regresses into a bare `if probe_failed`, a single timeout takes the
    whole fleet down.
    """
    assert '[[ "${state}" == "up" ]] && (( streak <= -DOWN_AFTER ))' in WATCHDOG
    assert '[[ "${state}" == "down" ]] && (( streak >= UP_AFTER ))' in WATCHDOG


def test_a_flipped_probe_result_resets_the_streak() -> None:
    """Alternating probes must never accumulate into an edge.

    Without the reset, ok/fail/ok/fail would walk a counter to the threshold
    and page (and pause) on a link that is merely noisy rather than down.
    """
    # failure after any run of successes restarts the negative run at -1
    assert "if (( streak > 0 )); then streak=-1; else streak=$(( streak - 1 )); fi" in WATCHDOG
    # success after any run of failures restarts the positive run at 1
    assert "if (( streak < 0 )); then streak=1; else streak=$(( streak + 1 )); fi" in WATCHDOG


def test_sustained_flap_escalation_is_disableable() -> None:
    """0 must switch the loud escalation off cleanly, matching the repo's
    established "0 = off" convention for optional rungs."""
    assert "(( SUSTAINED_FLAP_THRESHOLD > 0 )) || return 0" in WATCHDOG


def test_flap_coalescing_state_is_persisted() -> None:
    """Coalescing across invocations needs on-disk state; the timer runs the
    script fresh each interval, so in-memory counters would reset every tick
    and every edge would page."""
    for marker in ("flap_cooldown_until", "flap_count", "flap_since"):
        assert marker in WATCHDOG, f"missing flap state file: {marker}"


def test_a_flap_episode_is_not_cleared_while_the_brain_is_still_down() -> None:
    """THE PROPERTY THAT KEEPS ONE OUTAGE TO ONE MESSAGE PAIR.

    flap_summary_check runs every invocation and used to clear the whole
    episode — cooldown window, flap count, sustained-page re-arm — as soon as
    the cooldown timer elapsed, regardless of state. During a wedge whose down
    phase outlasts the cooldown that disarmed every gate mid-outage: the next
    edge was no longer inside a cooldown, so it alerted as a fresh outage, and
    the episode repeated one down + one up message per flap cycle for hours.

    An episode ends when the brain is UP. While it is down the window must be
    extended and nothing cleared, so the `rm -f` must be unreachable until the
    state is up.
    """
    body = WATCHDOG.split("flap_summary_check() {")[1].split("\n}")[0]

    guard = body.index('if [[ "${state}" != "up" ]]; then')
    extend = body.index('> "${FLAP_COOLDOWN_FILE}"', guard)
    early_return = body.index("return 0", extend)
    clear = body.index("rm -f", guard)

    assert extend < early_return < clear, \
        "while down, the cooldown window must be extended and the function must " \
        "return before anything is cleared"
    assert "${SUSTAINED_REARM_FILE}" in body[clear:], \
        "the sustained-page re-arm is episode state and must be cleared only on recovery"


def test_recovery_alert_disclaims_queue_verification() -> None:
    """The DOWN-edge message's "does not verify Kanban queue health" disclaimer
    is already pinned by test_queue_recovery_contract. Its UP-edge counterpart
    is not, and recovery is the riskier of the two to overclaim: a green
    "recovered" that implies the queue is draining invites the operator to
    stop looking."""
    assert "still requires queue verification" in WATCHDOG


def test_watchdog_is_enabled_by_default() -> None:
    """Regression guard for the 2026-08-01 flip. A silent revert back to false
    would leave the fleet with no auto-pause the next time the brain drops —
    exactly the incident that prompted enabling it."""
    assert "hermes_agent_brain_watchdog_enabled: true" in DEFAULTS


def test_probe_is_a_real_completion_not_a_liveness_endpoint() -> None:
    """THE TRAP THIS WATCHDOG EXISTS TO AVOID.

    A probe against /v1/models or any bare health endpoint can return 200 while
    the engine is wedged and generating nothing — confirmed live 2026-07-16,
    where a broken batch scheduler kept answering HTTP 200 with
    finish_reason=error and zero tokens. The probe must hit chat/completions
    with a real message body and verify BOTH a real finish_reason AND a
    nonzero completion_tokens count, not just a 200 status code.
    """
    assert 'PROBE_URL="${BASE_URL}/chat/completions"' in WATCHDOG
    assert '\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"ping\\"}]' in WATCHDOG
    assert '"finish_reason"' in WATCHDOG
    assert '"completion_tokens"' in WATCHDOG
    # A bare HTTP-200 check without the body assertions would silently pass a
    # wedged engine — the probe must require both.
    assert '[[ "${code}" == "200" ]] \\' in WATCHDOG


def test_pause_outcome_is_read_back_off_the_board_not_from_a_return_code() -> None:
    """THE FALSE-SUCCESS GUARD.

    `hermes cron pause <missing>` prints "Job ... not found" and still exits 0.
    A loop that scores the return code therefore counts a name with no job
    behind it as a pause: 8 of the 21 names carried no job, so every alert
    reported 21 pauses over a fleet of 13. The outcome must come from the
    board — what the job's state actually IS — never from what the command
    returned.
    """
    assert 'board="$("${HERMES_BIN}" cron list --all 2>/dev/null)"' in WATCHDOG
    # the pause/resume call is fired for effect only; its status is discarded
    assert '"${HERMES_BIN}" cron "${verb}" "${job}" >/dev/null 2>&1 || true' in WATCHDOG
    # ...and the counters are driven by the board lookup that follows
    assert 'grep -B1 -E "^ *Name: +${job}\\$" <<< "${board}"' in WATCHDOG


def test_board_readback_includes_paused_jobs() -> None:
    """`cron list` WITHOUT --all omits paused jobs entirely.

    Drop the flag and every pause verifies as a failure (the job it just paused
    has vanished from the output), and a paused job becomes indistinguishable
    from a deleted one. This one flag is what makes the readback above mean
    anything.
    """
    assert "cron list --all" in WATCHDOG
    assert 'want="paused"' in WATCHDOG


def test_watchdog_pauses_the_same_fleet_a_cluster_window_pauses() -> None:
    """hermes_agent_seeded_cron_names covers only the script-fed `-enqueue`
    crons, which never call the brain. The enabled `*-v2` jobs that DO call it
    live in hermes_agent_direct_cron_jobs, so pausing the seeded list alone
    left every brain-dependent job firing into a dead brain — the same gap
    found on the cluster-pause path 2026-07-25. Both paths must derive from
    one list so they cannot drift apart again.
    """
    assert "{{ hermes_agent_cluster_pause_cron_names | length }}" in WATCHDOG
    assert "{% for job in hermes_agent_cluster_pause_cron_names %}" in WATCHDOG


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
