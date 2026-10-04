"""Self-check for silent healthy runs and persistent-failure suppression.

Split from test_splunk_digest_deltas.py to stay under the token budget — see
_splunk_digest_shared.py for the loaded template/state fixtures and
test_splunk_digest_novelty.py for the per-day novelty/delta contract this
leaves behind.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_heartbeat.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
import datetime as dt

from _splunk_digest_shared import BASE, DIGEST, TEMPLATE, at, scale, step


def quiet_state():
    """Record a baseline and finding, then reach a quiet unchanged state."""
    grown = scale(BASE, "os", 1.6)
    _, s0 = step(BASE, None, 0)
    _, s1 = step(grown, s0, 1)
    _, s2 = step(grown, s1, 2)
    return grown, s2


def test_a_fully_quiet_digest_never_posts_a_heartbeat():
    grown, state = quiet_state()
    assert "HEARTBEAT_HOURS" not in TEMPLATE
    for hour in (3, 9, 21):
        text, state = step(grown, state, at(2) + dt.timedelta(hours=hour))
        assert text == DIGEST.SILENT


def test_an_older_state_file_still_reads_without_a_quiet_post():
    """The ledger's older key-only shape remains readable on a quiet run."""
    day = "2026-07-24"
    grown, state = quiet_state()
    # Rewrite the ledger into the OLD shape, keys only.
    state["ledger"]["keys"] = [e["k"] for e in state["ledger"]["keys"]]
    assert DIGEST.load_ledger(state, day) == state["ledger"]["keys"], \
        "the accessor must still read a pre-2026-07-29 ledger"

    text, _ = step(grown, state, at(2) + dt.timedelta(hours=12))
    assert text == DIGEST.SILENT


def test_a_persistent_failure_is_reported_once_not_every_run():
    """THE FLOOD. Until 2026-07-29 the failure path printed unconditionally —
    outside the ledger, outside SILENT, outside the per-day novelty rule the
    rest of this file obeys. An hourly cron turned one outage into 24 identical
    Slack messages a day, which is what made the channel unreadable.

    A fault that persists is not news after the first post."""
    now = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.timezone.utc)
    boom = RuntimeError("502 Bad Gateway from the Splunk MCP")

    first, state = DIGEST.failure_report(boom, None, now)
    assert "Splunk digest FAILED" in first, "the first sighting must always be delivered"
    assert "502 Bad Gateway" in first, "the post must name the actual cause"
    assert first != DIGEST.SILENT

    # Same fault, later run.
    later = now + dt.timedelta(hours=1)
    second, state = DIGEST.failure_report(boom, state, later)
    assert second == DIGEST.SILENT, "an unchanged failure must not re-post"

    third, state = DIGEST.failure_report(boom, state, later + dt.timedelta(hours=1))
    assert third == DIGEST.SILENT
    assert state["suppressed"]["count"] == 2, "suppression must be counted, not just dropped"


def test_a_changed_cause_posts_immediately():
    """Suppression is keyed on the fault, not on 'a failure happened'. A
    different cause is different news and must never be swallowed by the
    previous one's ledger entry."""
    now = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.timezone.utc)
    first, state = DIGEST.failure_report(RuntimeError("502 Bad Gateway"), None, now)
    assert first != DIGEST.SILENT

    changed, state = DIGEST.failure_report(
        RuntimeError("connection refused"), state, now + dt.timedelta(hours=1))
    assert changed != DIGEST.SILENT, "a different cause must post"
    assert "connection refused" in changed


def test_suppressed_repeats_are_surfaced_on_the_next_post():
    """Suppression must be VISIBLE, never silent. When a new cause finally
    posts, it carries how many repeats were held back and since when."""
    now = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.timezone.utc)
    _, state = DIGEST.failure_report(RuntimeError("502"), None, now)
    for hour in (1, 2, 3):
        _, state = DIGEST.failure_report(RuntimeError("502"), state, now + dt.timedelta(hours=hour))

    text, _ = DIGEST.failure_report(
        RuntimeError("a different fault"), state, now + dt.timedelta(hours=4))
    assert "3 identical repeat(s) suppressed since 09:00" in text, text


def test_a_new_utc_day_re_reports_a_still_broken_digest():
    """The ledger is day-scoped everywhere else in this file; the failure gate
    must not become a permanent mute for an outage that outlives a day."""
    day1 = dt.datetime(2026, 7, 29, 23, 0, tzinfo=dt.timezone.utc)
    boom = RuntimeError("502 Bad Gateway")
    _, state = DIGEST.failure_report(boom, None, day1)
    silent, state = DIGEST.failure_report(boom, state, day1 + dt.timedelta(minutes=30))
    assert silent == DIGEST.SILENT

    day2 = dt.datetime(2026, 7, 30, 8, 0, tzinfo=dt.timezone.utc)
    again, _ = DIGEST.failure_report(boom, state, day2)
    assert again != DIGEST.SILENT, "a new UTC day must re-report a still-failing digest"


def test_recovery_is_announced_once():
    """A fixed outage went quiet before this: the failure path posted forever
    and the success path knew nothing about it, so recovery was
    indistinguishable from the cron dying."""
    now = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.timezone.utc)
    _, state = DIGEST.failure_report(RuntimeError("502"), None, now)
    _, state = DIGEST.failure_report(RuntimeError("502"), state, now + dt.timedelta(hours=1))

    line = DIGEST.recovery_line(state, now + dt.timedelta(hours=2))
    assert line and "RECOVERED" in line, line
    assert "1 identical repeat(s) were suppressed" in line, line

    # main() clears the failure keys on a successful run; once cleared there is
    # nothing left to announce, so the notice fires exactly once.
    cleared = {k: v for k, v in state.items() if k not in ("failure", "suppressed")}
    assert DIGEST.recovery_line(cleared, now) is None


def test_a_failure_never_destroys_the_success_baseline():
    """The baseline belongs to the last SUCCESSFUL run. If a failure wiped it,
    recovery would report full numbers with no deltas and read as a fresh
    install — fabricating a change that never happened."""
    now = dt.datetime(2026, 7, 29, 9, 0, tzinfo=dt.timezone.utc)
    good = {"schema": DIGEST.STATE_SCHEMA, "by_index": {"os": {"volume": 10}},
            "captured_iso": "2026-07-29T08:00:00+00:00"}
    _, state = DIGEST.failure_report(RuntimeError("502"), good, now)
    assert state["by_index"] == {"os": {"volume": 10}}, "failure must not clobber the baseline"
    assert state["schema"] == DIGEST.STATE_SCHEMA


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
