"""Self-check for the hourly Splunk digest's percent-band, stability-line, and
schedule-vs-heartbeat contracts.

Split from test_splunk_digest_deltas.py to stay under the token budget — see
_splunk_digest_shared.py for the loaded template/state fixtures and
test_splunk_digest_novelty.py for the per-day novelty/delta contract this
leaves behind.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_bands.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
from _role_files import role_defaults
from _splunk_digest_shared import DIGEST, REPO_ROOT, at, step


def test_a_tiny_series_never_manufactures_a_percent_finding():
    assert DIGEST.band_of(9, 3) is None, "3 -> 9 events is +200% and means nothing"
    assert DIGEST.band_of(150, None) is None, "no baseline is never a move"
    assert DIGEST.band_of(1_500, 1_000)[0] == "up"
    assert DIGEST.band_of(1_500, 1_000)[1] == 50


def test_a_big_percent_of_a_small_number_is_not_a_finding():
    """The percent floor alone is not enough — the absolute move must matter too.

    109 -> 195 events in 24h clears MIN_MOVER_VOL and lands in the 50% band, so
    the percent gate reported it as "up 79%". For an audience of one it is not
    news. Both floors live in band_of, so this covers index-level and per-host
    movers alike.
    """
    assert DIGEST.band_of(195, 109) is None, "+86 events in 24h is not news at any percent"
    assert DIGEST.band_of(109 + DIGEST.MIN_MOVER_DELTA - 1, 109) is None, "just under the floor"
    # Same series, a move that clears the floor: still reported.
    assert DIGEST.band_of(109 + DIGEST.MIN_MOVER_DELTA, 109)[0] == "up"
    # The floor never rescues a series below the volume floor.
    assert DIGEST.band_of(3 + DIGEST.MIN_MOVER_DELTA, 3) is None
    # Symmetric on the way down.
    assert DIGEST.band_of(1_000, 1_200) is None, "-200 events is under the delta floor"
    assert DIGEST.band_of(1_000, 2_000)[0] == "down"


def test_stability_line_never_claims_a_change_against_nothing():
    now = at(18)
    fresh, _ = DIGEST.stability_finding(None, "fp1", now)
    assert "baseline established" in fresh.text and "CHANGED" not in fresh.text

    changed, _ = DIGEST.stability_finding({"fingerprint": "fp0"}, "fp1", now)
    assert "CHANGED" in changed.text

    held, first_seen = DIGEST.stability_finding(
        {"fingerprint": "fp1", "first_seen_iso": "2026-07-22T00:00:00+00:00"}, "fp1", now)
    assert "Day 3 since 2026-07-22" in held.text
    assert first_seen == "2026-07-22T00:00:00+00:00", "stable state must keep its original first_seen"


def test_host_field_absence_is_reported_as_unavailable_not_zero():
    hostless = [{"index": "os", "vol": "500", "last_time": str(int(at(0).timestamp()) - 60)}]
    text, state = step(hostless, None, 0)
    assert "hosts: 0" in text, "a missing host field must not invent hosts"
    assert state["host_detail"] is False and state["st_detail"] is False


def test_waking_hours_schedule_never_outruns_the_heartbeat_gate():
    """The run window and HEARTBEAT_HOURS must stay compatible.

    The digest runs on a waking-hours schedule, so there is a nightly gap where
    no run happens at all. That is fine only while the FIRST run after the gap
    is guaranteed to be at least HEARTBEAT_HOURS past the last one — otherwise
    the morning could open with a `[SILENT]` run and the operator would see
    nothing at all until the gate happened to trip. This pins the two values
    against each other so raising HEARTBEAT_HOURS or narrowing the window
    cannot silently break that guarantee.
    """
    defaults = role_defaults(REPO_ROOT / "roles" / "hermes_agent")
    schedule = defaults["hermes_agent_splunk_status_digest_cron_schedule"]

    minute, hours = schedule.split()[0], schedule.split()[1]
    assert hours != "*", (
        "schedule is back to 24/7; if that is intended, delete this check "
        "rather than loosening it")
    start, end = (int(part) for part in hours.split("-"))
    # Longest stretch with no run: from the last run of one day to the first of
    # the next. Minutes are identical on both ends, so hours alone decide it.
    gap_hours = 24 - end + start
    assert gap_hours >= DIGEST.HEARTBEAT_HOURS, (
        f"the {gap_hours}h overnight gap in {schedule!r} is shorter than "
        f"HEARTBEAT_HOURS={DIGEST.HEARTBEAT_HOURS}, so the first run of the day "
        "can be gated to [SILENT] and the morning opens with no state report")
    assert 0 <= int(minute) <= 59 and 0 <= start < end <= 23, schedule


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
