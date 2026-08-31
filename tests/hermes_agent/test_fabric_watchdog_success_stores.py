"""fabric_watchdog absence-of-success contract ACROSS job stores.

The scheduler runs more than one root, each with its own store, and any of them
can be deliberately stopped. Both facts change what silence means, and both were
invisible to a guard that read one store and treated every gap as a stall.

The single-store contract — success-vs-run, per-cadence thresholds, the latch —
lives in test_fabric_watchdog_success_absence.py.
"""

from __future__ import annotations

from _fabric_watchdog_shared import (
    MINUTE,
    NOW,
    TMP,
    WD,
    job,
    log_lines,
    run,
    settle,
)


# --- More than one scheduler root --------------------------------------------


def test_a_stale_job_in_a_non_default_profile_fires() -> None:
    """The regression this section exists for. Reading only the default root
    leaves every profile-scoped job unwatched — and, worse, reports the fleet
    healthy while doing it, which is a guard that lies rather than one that is
    merely absent.
    """
    healthy = [job("audit", cadence_min=15, last_ok_ago_min=5)]
    state = settle([], profiles={"homelab-admin": healthy})
    stale, _, _, _ = run([], state, now=NOW + 175 * MINUTE, profiles={"homelab-admin": healthy})
    assert len(stale) == 1 and stale[0].startswith("homelab-admin/audit "), (
        "a stalled job outside the default root must page, and the message must "
        "name its profile or nobody can act on it"
    )


def test_same_named_jobs_in_two_profiles_do_not_collide() -> None:
    """Job names are unique only WITHIN a store. Keyed on the name alone, two
    profiles sharing one share a single state record: the healthy job keeps
    advancing the last-success clock, so the stalled one can never page.
    """
    both_healthy = {
        "homelab-admin": [job("digest", cadence_min=15, last_ok_ago_min=1)],
        "splunk-admin": [job("digest", cadence_min=15, last_ok_ago_min=1)],
    }
    state = settle([], profiles=both_healthy)
    stale, _, _, _ = run(
        [],
        state,
        now=NOW + 200 * MINUTE,
        profiles={
            # Still succeeding punctually, right up to the later tick.
            "homelab-admin": [job("digest", cadence_min=15, last_ok_ago_min=-199)],
            # Not since the baseline tick.
            "splunk-admin": [job("digest", cadence_min=15, last_ok_ago_min=1)],
        },
    )
    assert len(stale) == 1, f"exactly the stalled profile's job pages: {stale}"
    assert stale[0].startswith("splunk-admin/digest "), f"the wrong profile paged: {stale}"


def test_a_profile_without_a_job_store_is_skipped_silently(capsys) -> None:
    """A profile root that carries no store yet is a normal state, not an error.
    Reading one unconditionally would log a failure every tick forever — the
    kind of standing noise that trains an operator to ignore this guard.
    """
    healthy = [job("audit", cadence_min=15, last_ok_ago_min=5)]
    run([], profiles={"github-maint": None, "homelab-admin": healthy})
    lines = log_lines(capsys)
    assert not [line for line in lines if "unreadable" in line], (
        f"a storeless profile must not produce an error line: {lines}"
    )
    assert not [line for line in lines if "github-maint" in line], (
        f"a storeless profile must not be mentioned at all: {lines}"
    )
    assert any(line.startswith("homelab-admin/audit: ") for line in lines), (
        "a storeless profile must not stop the stores that do exist from being read"
    )


def test_healthy_jobs_across_every_store_stay_quiet() -> None:
    """The other direction across roots: reading more stores must not make the
    guard page on jobs that are fine.
    """
    profiles = {
        "homelab-admin": [job("audit", cadence_min=60, last_ok_ago_min=20)],
        "splunk-admin": [job("sweep", cadence_min=1440, last_ok_ago_min=300)],
        "github-maint": None,
    }
    default = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(default, profiles=profiles)
    stale, recovered, _, _ = run(default, state, profiles=profiles)
    assert stale == [] and recovered == []


# --- A deliberately stopped store --------------------------------------------
#
# A converge drains in-flight work before restarting, and an operator can stop a
# store by hand. Both leave a real gap in successful runs that exceeds this
# guard's floor. Paging for it would be the guard's own undoing: an alarm that
# fires during planned maintenance is one operators learn to dismiss, and the
# credibility spent on a false page is not recovered by a later true one.


def test_a_stalled_job_in_a_stopped_store_reports_paused_instead_of_paging(capsys) -> None:
    healthy = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(healthy)
    stale, _, _, _ = run(healthy, state, now=NOW + 175 * MINUTE, paused=["default"])
    assert stale == [], "a store that deliberately stopped explains its own silence"
    assert any("PAUSED" in line for line in log_lines(capsys)), (
        "suppression must still be a logged decision, not a silent skip"
    )


def test_an_empty_stop_sentinel_still_counts_as_stopped() -> None:
    """Presence is the whole signal. A writer interrupted mid-write leaves an
    empty or truncated file, and deciding engagement from a body this guard does
    not own would fail open at exactly that moment.
    """
    run([])
    (TMP / "ESTOP").write_text("")
    try:
        assert [paused for _, paused, _ in WD.load_stores()] == [True]
    finally:
        (TMP / "ESTOP").unlink()


def test_stopping_one_store_does_not_silence_another() -> None:
    stale_job = [job("audit", cadence_min=15, last_ok_ago_min=5)]
    state = settle(stale_job, profiles={"homelab-admin": stale_job})
    stale, _, _, _ = run(
        stale_job,
        state,
        now=NOW + 175 * MINUTE,
        profiles={"homelab-admin": stale_job},
        paused=["default"],
    )
    assert len(stale) == 1 and stale[0].startswith("homelab-admin/audit "), (
        f"only the stopped store is suppressed: {stale}"
    )


def test_a_job_already_stale_before_the_pause_pages_as_soon_as_it_lifts() -> None:
    """The pause explains the paused interval and nothing else. A job that had
    already stopped succeeding before the stop went in is still broken after it
    lifts, and must page then rather than start over.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 200 * MINUTE, paused=["default"])
    stale, _, _, _ = run(jobs, state, now=NOW + 210 * MINUTE)
    assert len(stale) == 1, "200m of pre-pause silence does not stop being silence"


def test_a_healthy_job_is_not_charged_for_the_time_the_store_was_stopped() -> None:
    """The other direction, and the reason banking beats simply resuming the
    clock: a job that was fine going in must not page on the way out for a
    window it could not have run in. 45m stopped, 6m of its own silence.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 5 * MINUTE, paused=["default"])
    stale, _, _, _ = run(jobs, state, now=NOW + 50 * MINUTE)
    assert stale == [], "51m of wall clock minus 45m stopped is not a stall"


def test_repeated_pause_cycles_cannot_mask_a_job_that_has_stopped_succeeding() -> None:
    """Only real stopped time is discounted, and it accumulates honestly. A job
    dead the whole time still crosses its threshold on the wall clock the pauses
    do not cover — otherwise a cycling pause is a way to hide a dead fleet.

    Timed so the stall is still inside its 45m threshold at the last resume
    (41m of banked-adjusted silence) and crosses it 20m later. A guard that
    restarted the clock on resume would read 20m at that final tick and stay
    quiet; only the banked total exposes the real 61m of silence.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    for start, end in ((10, 20), (50, 60)):
        stale, _, state, _ = run(jobs, state, now=NOW + start * MINUTE, paused=["default"])
        assert stale == [], f"stopped at {start}m must be quiet"
        stale, _, state, _ = run(jobs, state, now=NOW + end * MINUTE)
        assert stale == [], f"resumed at {end}m is still inside the threshold"
    stale, _, _, _ = run(jobs, state, now=NOW + 80 * MINUTE)
    assert len(stale) == 1, "61m of silence with only 20m stopped is still a stall"


def test_a_store_left_stopped_indefinitely_raises_its_own_alert_once() -> None:
    """Suppression cannot be unbounded. Past the threshold the pause has stopped
    explaining the silence and has become the thing nobody has accounted for.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    _, _, state, opening = run(jobs, now=NOW, paused=["default"])
    assert opening == [], "a pause that just started is not yet a problem"
    _, _, state, late = run(jobs, state, now=NOW + 241 * MINUTE, paused=["default"])
    assert len(late) == 1 and late[0].startswith("default "), f"{late}"
    _, _, _, again = run(jobs, state, now=NOW + 300 * MINUTE, paused=["default"])
    assert again == [], "the pause alert must not become the noise it replaces"
