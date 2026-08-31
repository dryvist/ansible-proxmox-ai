"""fabric_watchdog absence-of-success contract, within a single job store.

The role's endpoint probes answer "is the fabric reachable" and stay green while
every scheduled job fails or stops firing altogether. A fleet in that state
posts an error per run — indistinguishable from ordinary noise — so nothing
pages. The doctrine that covers it: alert on the absence of success, not just on
errors.

These tests run the shipped script against a synthetic job store rather than
asserting on its text, and pin BOTH directions: it fires on a stalled job and
stays silent on a healthy one. A guard only ever observed staying quiet is not
evidence of anything.

Behaviour spanning MORE than one store — profile-scoped roots, and stores
deliberately stopped — lives in test_fabric_watchdog_success_stores.py.

Lives under tests/hermes_agent/ because fabric_watchdog runs on the Hermes guest
and reads that role's cron job store (see test_fabric_watchdog_debounce.py).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from _fabric_watchdog_shared import (
    DEFAULTS,
    FIXTURE_CONFIG,
    MINUTE,
    NOW,
    WD,
    job,
    log_lines,
    run,
    settle,
)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


# --- Fires when it should ----------------------------------------------------


def test_a_job_that_stopped_running_fires() -> None:
    """The core case: no error, no run, nothing but silence. A 15-minute job
    whose last success is ~3 hours old has missed a dozen cycles.
    """
    healthy = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(healthy)
    stale, _, _, _ = run(healthy, state, now=NOW + 175 * MINUTE)
    assert len(stale) == 1 and stale[0].startswith("default/digest ")


def test_a_job_failing_every_run_fires_even_though_last_run_at_stays_fresh() -> None:
    """The trap this exists for. `last_run_at` records a RUN, not a SUCCESS, so
    a job erroring on every tick keeps it perpetually fresh. Reading the store's
    timestamp directly would call that job healthy forever.
    """
    state = settle([job("triage", cadence_min=15, last_ok_ago_min=1)])
    later = NOW + 200 * MINUTE
    # Still running punctually — and failing every time.
    failing = [job("triage", cadence_min=15, last_ok_ago_min=-199, status="error")]
    stale, _, _, _ = run(failing, state, now=later)
    assert len(stale) == 1, "a job that runs but never succeeds must page"


def test_a_mass_stall_arrives_as_one_message_not_one_per_job() -> None:
    healthy = [job(f"j{i}", cadence_min=15, last_ok_ago_min=1) for i in range(6)]
    state = settle(healthy)
    stale, _, _, _ = run(healthy, state, now=NOW + 300 * MINUTE)
    assert len(stale) >= FIXTURE_CONFIG["FLEET_ROLLUP_AT"], (
        "six simultaneously stale jobs must reach the roll-up threshold, or the "
        "fleet-death case posts six separate pages"
    )


# --- Quiet when it should ----------------------------------------------------


def test_a_healthy_fleet_says_nothing() -> None:
    jobs = [
        job("fast", cadence_min=15, last_ok_ago_min=5),
        job("hourly", cadence_min=60, last_ok_ago_min=20),
        job("daily", cadence_min=1440, last_ok_ago_min=300),
    ]
    state = settle(jobs)
    stale, recovered, _, _ = run(jobs, state)
    assert stale == [] and recovered == []


def test_one_late_run_does_not_page() -> None:
    """MISSED_CYCLES > 1 exists so a single late run or one retry is absorbed."""
    state = settle([job("hourly", cadence_min=60, last_ok_ago_min=5)])
    stale, _, _, _ = run(
        [job("hourly", cadence_min=60, last_ok_ago_min=5)], state, now=NOW + 65 * MINUTE
    )
    assert stale == [], "a job one cycle late is late, not silent"


def test_a_daily_job_is_not_judged_by_a_frequent_jobs_clock() -> None:
    """The per-cadence rule's whole point: 90 minutes of silence is a stall for a
    15-minute job and entirely normal for a daily one.
    """
    daily = [job("daily", cadence_min=1440, last_ok_ago_min=5)]
    quarter = [job("quarter", cadence_min=15, last_ok_ago_min=5)]
    later = NOW + 90 * MINUTE
    assert run(daily, settle(daily), now=later)[0] == []
    assert len(run(quarter, settle(quarter), now=later)[0]) == 1


def test_a_disabled_job_is_not_silence() -> None:
    state = settle([job("seasonal", cadence_min=15, last_ok_ago_min=1)])
    off = [job("seasonal", cadence_min=15, last_ok_ago_min=1, enabled=False)]
    stale, _, _, _ = run(off, state, now=NOW + 999 * MINUTE)
    assert stale == [], "deliberately disabled is not a failure to succeed"


def test_a_brand_new_job_gets_one_full_threshold_before_it_can_page(capsys) -> None:
    """Seeding at `now` rather than paging immediately. Otherwise every deploy
    onto an already-broken fleet opens with an alert storm for silence that
    predates any observation this watchdog made.
    """
    stale, _, _, _ = run([job("newborn", cadence_min=15, last_ok_ago_min=999, status="error")])
    assert stale == []
    assert any("SEED" in line for line in log_lines(capsys))


# --- Re-alert suppression ----------------------------------------------------


def test_an_ongoing_stall_alerts_once_not_every_tick() -> None:
    """The alert must not become the noise it replaces."""
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    first, _, state, _ = run(jobs, state, now=NOW + 200 * MINUTE)
    assert len(first) == 1
    for tick in range(1, 6):
        again, _, state, _ = run(jobs, state, now=NOW + (200 + tick * 2) * MINUTE)
        assert again == [], f"tick {tick} re-announced an already-alerted stall"


def test_recovery_clears_the_latch_so_the_next_stall_can_page_again() -> None:
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    run(jobs, state, now=NOW + 200 * MINUTE)
    healthy = [job("digest", cadence_min=15, last_ok_ago_min=-201)]
    _, recovered, state, _ = run(healthy, state, now=NOW + 205 * MINUTE)
    assert recovered == ["default/digest"]
    stale, _, _, _ = run(healthy, state, now=NOW + 500 * MINUTE)
    assert len(stale) == 1, "a latch that never clears silences every later stall"


# --- Logging and threshold contracts -----------------------------------------


def test_every_job_gets_a_decision_line_every_tick(capsys) -> None:
    """A guard that exits silently hides the halt it exists to catch."""
    jobs = [job(f"j{i}", cadence_min=15, last_ok_ago_min=1) for i in range(4)]
    state = settle(jobs)
    log_lines(capsys)  # discard the baseline tick, or IT could satisfy the assertion
    run(jobs, state)
    lines = log_lines(capsys)
    for i in range(4):
        assert any(line.startswith(f"default/j{i}: ") for line in lines), f"j{i} evaluated in silence"


def test_the_threshold_derives_from_the_jobs_own_cadence() -> None:
    quarter = WD.threshold_seconds(WD.cadence_seconds(job("a", cadence_min=15, last_ok_ago_min=0)))
    hourly = WD.threshold_seconds(WD.cadence_seconds(job("b", cadence_min=60, last_ok_ago_min=0)))
    assert hourly > quarter, "a slower job must be given a longer threshold"
    assert hourly == 60 * MINUTE * FIXTURE_CONFIG["MISSED_CYCLES"]


def cron_job(name, *, cadence_min, last_ago_min, next_in_min, status="ok"):
    """A cron-kind record, whose schedule carries an expression and no `minutes`.

    last_run_at and next_run_at are set independently so the re-armed case —
    a stale last run with an advancing next run — can be built.
    """
    return {
        "id": name, "name": name, "enabled": True,
        "schedule": {"kind": "cron", "expr": f"26 */{cadence_min // 60} * * *"},
        "last_run_at": _iso(NOW - last_ago_min * MINUTE),
        "next_run_at": _iso(NOW + next_in_min * MINUTE),
        "last_status": status,
    }


def test_a_cron_expression_job_derives_its_cadence_without_a_cron_parser() -> None:
    """Cron-kind jobs carry no `minutes`, so the cadence comes from the gap the
    scheduler already computed between last_run_at and next_run_at.
    """
    every_6h = cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=360)
    assert WD.cadence_seconds(every_6h) == 360 * MINUTE
    assert WD.threshold_seconds(WD.cadence_seconds(every_6h)) == 18 * 60 * MINUTE


def test_a_rearmed_job_cannot_raise_its_own_threshold_by_staying_broken() -> None:
    """The gap is only one cadence when measured off a COMPLETED run. A job
    re-armed without running leaves last_run_at stale while next_run_at advances,
    so the live gap reads as the whole outage. Deriving the threshold from that
    would make the guard QUIETER the longer the job stayed broken — an anti-guard.
    The cadence pinned on the last success is what must be used instead.
    """
    healthy = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=360)]
    state = settle(healthy)

    # 20h later: never ran again, but re-armed repeatedly. The live gap is now
    # ~26h, which alone would clamp the threshold to the 24h ceiling.
    drifted = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=1560, status="error")]
    assert WD.cadence_seconds(drifted[0]) == 1560 * MINUTE, "premise: the live gap is inflated"

    stale, _, _, _ = run(drifted, state, now=NOW + 20 * 60 * MINUTE)
    assert len(stale) == 1, (
        "20h of silence must page a 6h job at its pinned 18h threshold, not be "
        "deferred to 24h by a gap the outage itself inflated"
    )


def test_the_pinned_cadence_does_not_make_the_guard_trigger_happy() -> None:
    """The other direction: pinning must not shrink the threshold either. The
    same job at 17h of silence is still inside its 18h window.
    """
    healthy = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=360)]
    state = settle(healthy)
    drifted = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=1500, status="error")]
    stale, _, _, _ = run(drifted, state, now=NOW + 17 * 60 * MINUTE)
    assert stale == []


def test_thresholds_match_the_shipped_defaults() -> None:
    """Guard the values, not just the code shape — otherwise the per-cadence
    rule can be flattened back to a global one in a defaults tweak with every
    other test still green.
    """
    for name, fixture in FIXTURE_CONFIG.items():
        if not isinstance(fixture, int):
            continue
        var = "fabric_watchdog_success_" + name.lower()
        match = re.search(rf"^{var}:\s*(\d+)", DEFAULTS, re.M)
        assert match is not None, f"{var} missing from defaults — renamed?"
        assert int(match.group(1)) == fixture, f"{var} drifted from its self-check fixture"
    assert FIXTURE_CONFIG["MISSED_CYCLES"] >= 2, (
        "missed_cycles=1 pages on a single late run, which is the error-rate "
        "alerting this watchdog was built to replace"
    )

