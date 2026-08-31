"""Pool-wait gauge: time from a job's scheduled slot to its actual start.

Distinct from the absence-of-success clock in test_fabric_watchdog_success_
absence.py, which watches SILENCE (no success for MISSED_CYCLES x cadence —
hours, by design, to absorb ordinary lateness without paging). A job held
out of its store's single-threaded cron pool by a store-mate can miss its
slot by minutes without ever tripping that: no error is raised (nothing ran
to error), and MISSED_CYCLES x cadence hasn't elapsed. This gauge watches
the faster, more specific signal instead: has THIS job's own next_run_at
already passed while it's still sitting unclaimed.

Threshold is the job's OWN cadence, not a sibling's — the category error an
earlier draft of this fix made was deriving one job's runtime ceiling from a
DIFFERENT job's schedule. test_wait_threshold_is_independent_of_sibling_jobs
below asserts that directly.
"""

from __future__ import annotations

from _fabric_watchdog_shared import MINUTE, NOW, job, run, settle


def test_a_job_overdue_past_its_own_cadence_is_flagged() -> None:
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 26 * MINUTE)
    assert len(state["waited"]) == 1
    assert state["waited"][0].startswith("default/digest ")


def test_a_job_late_by_less_than_its_own_cadence_is_not_flagged() -> None:
    """Mirrors test_one_late_run_does_not_page: one late run is late, not a
    pool-contention symptom worth paging on yet.
    """
    jobs = [job("hourly", cadence_min=60, last_ok_ago_min=5)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 65 * MINUTE)
    assert state["waited"] == []


def test_the_wait_clears_once_the_job_catches_up() -> None:
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 26 * MINUTE)
    assert len(state["waited"]) == 1

    caught_up = [job("digest", cadence_min=15, last_ok_ago_min=-11)]
    _, _, state, _ = run(caught_up, state, now=NOW + 26 * MINUTE)
    assert state["waited"] == []


def test_an_ongoing_wait_alerts_once_not_every_tick() -> None:
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 26 * MINUTE)
    assert len(state["waited"]) == 1
    for tick in range(1, 4):
        _, _, state, _ = run(jobs, state, now=NOW + (26 + tick) * MINUTE)
        assert state["waited"] == [], f"tick {tick} re-announced an already-alerted wait"


def test_a_disabled_job_is_never_flagged_as_waiting() -> None:
    jobs = [job("seasonal", cadence_min=15, last_ok_ago_min=5, enabled=False)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 26 * MINUTE)
    assert state["waited"] == []


def test_a_job_with_no_observed_success_yet_is_seeded_not_flagged() -> None:
    """The SEED path (no success ever observed) runs and `continue`s before
    the wait check — a genuinely new or already-broken job gets one full
    absence-of-success threshold to prove itself before ANYTHING pages on
    it, exactly as it already does for staleness.
    """
    jobs = [job("newborn", cadence_min=15, last_ok_ago_min=999, status="error")]
    _, _, state, _ = run(jobs, now=NOW)
    assert state["waited"] == []


def test_wait_threshold_is_independent_of_sibling_jobs() -> None:
    """The category error this gauge exists to avoid, made executable: job A's
    wait/alert outcome must be byte-identical whether or not a much-faster
    sibling job B is even in the same store.
    """
    a_alone = [job("slow", cadence_min=15, last_ok_ago_min=5)]
    a_with_sibling = [
        job("slow", cadence_min=15, last_ok_ago_min=5),
        job("fast", cadence_min=1, last_ok_ago_min=0),
    ]

    state_alone = settle(a_alone)
    _, _, state_alone, _ = run(a_alone, state_alone, now=NOW + 26 * MINUTE)

    state_with = settle(a_with_sibling)
    _, _, state_with, _ = run(a_with_sibling, state_with, now=NOW + 26 * MINUTE)

    slow_alone = [e for e in state_alone["waited"] if e.startswith("default/slow ")]
    slow_with = [e for e in state_with["waited"] if e.startswith("default/slow ")]
    assert slow_alone == slow_with
    assert len(slow_alone) == 1


def test_a_fast_sibling_cannot_tighten_a_slower_jobs_threshold() -> None:
    """The discriminating case the test above alone cannot catch: a wait long
    enough to clear a fast sibling's cadence but short of the job's OWN.
    Deriving the threshold from the fleet's tightest cadence instead of the
    job's own (the rejected design) would alert here; the job's own cadence
    must not.
    """
    jobs = [
        job("slow", cadence_min=20, last_ok_ago_min=5),
        job("fast", cadence_min=1, last_ok_ago_min=0),
    ]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 20 * MINUTE)
    assert [e for e in state["waited"] if e.startswith("default/slow ")] == []
