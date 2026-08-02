"""Safety contract for the Hermes brain watchdog.

WHY THIS EXISTS. `hermes_agent_brain_watchdog_enabled` defaults to false, and
the roadmap calls for turning it on. Before that happens the watchdog's
decision logic needs a contract, because it does not merely alert: it
`pause`/`resume`s the role-seeded cron fleet. A watchdog that pauses the whole
fleet on a single unlucky probe would silently stop all Hermes work, and the
symptom — nothing running — looks identical to an idle board.

The template already implements debounce, flap coalescing and a sustained-flap
escalation. Nothing pinned them. `test_queue_recovery_contract.py` does touch
this template, but only to assert the ALERT TEXT is honest (that it reports
command outcomes and disclaims queue health). The logic that decides whether
to pause the fleet at all — the streak debounce and the flap coalescing — had
no coverage. That gap is what this file closes; the alert-honesty assertions
are deliberately left where they already live rather than duplicated here.

These are static assertions against the template text, matching the style used
by test_alert_routing.py and test_goal_mode_contract.py. They pin the
PROPERTIES that make enabling safe, not the exact numbers — the numbers are
role variables and an operator may tune them.
"""

from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
WATCHDOG = (ROLE / "templates" / "hermes-brain-watchdog.sh.j2").read_text()
DEFAULTS = (ROLE / "defaults" / "main.yml").read_text()


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


def test_recovery_alert_disclaims_queue_verification() -> None:
    """The DOWN-edge message's "does not verify Kanban queue health" disclaimer
    is already pinned by test_queue_recovery_contract. Its UP-edge counterpart
    is not, and recovery is the riskier of the two to overclaim: a green
    "recovered" that implies the queue is draining invites the operator to
    stop looking."""
    assert "still requires queue verification" in WATCHDOG
