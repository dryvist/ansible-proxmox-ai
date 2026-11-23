"""Self-check for the Splunk triage repeat floors (operator rule, 2026-11-23).

The SAME finding posts at most once per ROUTINE_REPEAT_MIN (rolling 24h), or
once per CRITICAL_REPEAT_MIN (4h) when critical. A finding's identity is the
fault, never the count band it landed in, and the window is rolling, so the
UTC day boundary never shortens it. See test_splunk_triage_novelty.py for the
novelty contract these floors gate.

Runs bare (`python3 tests/hermes_agent/test_splunk_triage_repeat_floor.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
import datetime as dt

from _splunk_triage_shared import OTLP, RAFT, TRIAGE, at, rows


def test_an_oscillating_escalation_posts_once_per_critical_floor():
    low, high = rows({OTLP: {"open-webui": 90}}), rows({OTLP: {"open-webui": 3100}})
    _, state = TRIAGE.build_report(low, at(1), None)
    text, state = TRIAGE.build_report(high, at(2), state)
    assert "ESCALATING" in text
    for hour in (3, 4, 5):          # back down, then up again inside 4h
        _, state = TRIAGE.build_report(low, at(hour), state)
        text, state = TRIAGE.build_report(high, at(hour) + dt.timedelta(minutes=30), state)
        assert "ESCALATING" not in text, f"the same escalation reposted at {hour}:08"
    _, state = TRIAGE.build_report(low, at(6), state)
    text, _ = TRIAGE.build_report(high, at(6) + dt.timedelta(minutes=30), state)
    assert "ESCALATING" in text, "past the 4h floor the escalation may post again"


def test_a_steady_signature_crossing_a_band_edge_is_not_a_new_issue():
    small, big = rows({RAFT: {"openbao-02": 900}}), rows({RAFT: {"openbao-02": 1100}})
    text, state = TRIAGE.build_report(big, at(1), None)
    assert "storage.raft" in text
    text, state = TRIAGE.build_report(small, at(2), state)
    assert "storage.raft" not in text, "falling a band is the same steady fault"


def test_the_utc_day_boundary_does_not_shorten_the_routine_floor():
    data = rows({RAFT: {"openbao-02": 400}})
    late = at(23)
    text, state = TRIAGE.build_report(data, late, None)
    assert "storage.raft" in text
    text, state = TRIAGE.build_report(data, late + dt.timedelta(hours=2), state)
    assert "storage.raft" not in text, "two hours later is inside 24h, whatever the date"
    text, _ = TRIAGE.build_report(data, late + dt.timedelta(hours=24), state)
    assert "storage.raft" in text, "24h on, routine information may be presented again"


def test_a_failure_posts_once_per_routine_floor_and_keeps_the_baseline():
    _, state = TRIAGE.build_report(rows({RAFT: {"openbao-02": 400}}), at(1), None)
    first, state2 = TRIAGE.failure_report("McpError: connection refused", state, at(2))
    assert "FAILED" in first and first.startswith("[ISSUES]")
    assert state2["counts"] == state["counts"], "a failure must not wipe the baseline"
    again, _ = TRIAGE.failure_report("McpError: connection refused", state2, at(3))
    assert again == TRIAGE.SILENT, "the same outage must not post every run"
    other, _ = TRIAGE.failure_report("TimeoutError: read timed out", state2, at(3))
    assert "FAILED" in other, "a different cause posts at once"


def test_an_older_day_scoped_ledger_still_suppresses_after_the_upgrade():
    data = rows({RAFT: {"openbao-02": 400}})
    _, state = TRIAGE.build_report(data, at(1), None)
    state["ledger"]["keys"] = [entry["k"] for entry in state["ledger"]["keys"]]
    text, _ = TRIAGE.build_report(data, at(2), state)
    assert "storage.raft" not in text, "keys from the old ledger shape still count as posted"


def test_band_suffixed_keys_from_before_the_upgrade_still_count_as_posted():
    data = rows({RAFT: {"openbao-02": 400}})
    _, state = TRIAGE.build_report(data, at(1), None)
    state["ledger"]["keys"] = [f"{entry['k']}:2" for entry in state["ledger"]["keys"]]
    text, _ = TRIAGE.build_report(data, at(2), state)
    assert "storage.raft" not in text, "an old band suffix must not make it a new issue"
    assert TRIAGE.legacy_key("share:syslog:-2") == "share:syslog:down"
    assert TRIAGE.legacy_key("host:openbao-02:up:50") == "host:openbao-02:up"
    assert TRIAGE.legacy_key("host:openbao-02:up") == "host:openbao-02:up"


def test_a_signature_seen_yesterday_is_not_new_after_midnight():
    both = rows({RAFT: {"openbao-02": 400}, OTLP: {"open-webui": 90}})
    _, state = TRIAGE.build_report(both, at(22), None)
    _, state = TRIAGE.build_report(rows({RAFT: {"openbao-02": 400}}), at(23), state)
    text, _ = TRIAGE.build_report(both, at(1, day=25), state)
    assert "*NEW*" not in text, "a known fault must not post as new because the date changed"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ok")
