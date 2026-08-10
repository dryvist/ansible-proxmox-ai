"""Self-check for the hourly Splunk digest's per-day novelty gate + deltas.

Split from test_splunk_digest_deltas.py to stay under the token budget — see
_splunk_digest_shared.py for the loaded template/state fixtures and
test_splunk_digest_heartbeat.py for the heartbeat-restatement and
persistent-failure contracts this leaves behind.

Two contracts, both enforced here:

1. Per-day novelty — routine information may be presented ONCE per UTC day. A
   later run with nothing genuinely new must escalate its search (host level, then
   sourcetype/composition) rather than re-present it or emit boilerplate. Critical
   conditions are exempt and repeat every run while they hold.
2. Never fabricate — deltas are computed, an absent baseline says so, ingest
   silence is always explicit, and the fact path is one tstats call with no LLM.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_novelty.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
import datetime as dt
import json
from pathlib import Path

from _splunk_digest_shared import (
    BASE,
    DIGEST,
    STATE_DIR,
    TEMPLATE,
    at,
    ledger_keys,
    scale,
    step,
    with_stale_host,
)


def test_first_run_of_a_day_posts_the_full_baseline_table():
    text, state = step(BASE, None, 0)

    assert "First digest of 2026-07-24" in text
    assert "1,200,000" in text, "the day's baseline table must carry real volumes"
    assert "400,000" in text and "50,000" in text
    assert "no baseline" in text, "delta cells must say why a delta is missing"
    assert "No prior baseline" in text
    assert state["ledger"]["day"] == "2026-07-24"
    assert "table:2026-07-24" in ledger_keys(state)
    assert state["by_index"]["os"] == {
        "vol": 1_200_000, "hosts": 2,
        "host_vol": {"host-a": 1_000_000, "host-b": 200_000},
        "st_vol": {"syslog": 1_200_000},
    }


def test_a_routine_finding_already_posted_today_is_suppressed():
    _, s0 = step(BASE, None, 0)
    grown = scale(BASE, "os", 1.45)          # +45% -> a real index-level finding

    second, s1 = step(grown, s0, 1)
    assert "index=os volume up 45%" in second, "a novel routine move must be posted"
    assert "First digest of" not in second, "the daily table is routine — once per day only"
    assert "1,740,000" in second
    key = next(k for k in ledger_keys(s1) if k.startswith("vol:os:up"))

    # The same +45% band an hour later against the new baseline: same identity.
    third, s2 = step(scale(grown, "os", 1.45), s1, 2)
    assert ledger_keys(s2).count(key) == 1, "a repeat must not be re-ledgered"
    assert "index=os volume up 45%" not in third, \
        "a routine finding already presented today must be suppressed"


def test_a_persisting_critical_condition_repeats_every_run():
    dark = {i: h for i, h in BASE.items() if i != "network"}
    first, s0 = step(dark, None, 0)
    second, s1 = step(dark, s0, 1)
    third, _ = step(dark, s1, 2)

    for run, text in enumerate((first, second, third)):
        assert "INGEST SILENCE: index=network" in text, \
            f"run {run}: a persisting critical condition must repeat, never be ledgered"
        assert "Critical — repeats every run" in text
    assert not any(k.startswith("crit:") for k in ledger_keys(s1)), \
        "critical findings must never enter the day ledger"


def test_an_exhausted_routine_search_escalates_before_it_gives_up():
    _, s0 = step(BASE, None, 0)

    # Identical data: the only index-level news is that everything is flat.
    flat, s1 = step(BASE, s0, 1)
    assert "byte-identical" in flat and "index level" in flat

    # Still identical, but one host's newest event is now 3h old -> host tier.
    escalated, s2 = step(with_stale_host(2, "host-b", hours=3), s1, 2)
    assert "host level" in escalated and "nothing new at index level" in escalated
    assert "host host-b" in escalated and "3.0 h old" in escalated

    # Nothing left anywhere, and the heartbeat ceiling has elapsed since the
    # last real post (hour 2): an honest, specific line — never bare
    # boilerplate, and never silent once the heartbeat is due.
    exhausted, _ = step(with_stale_host(9, "host-b", hours=3), s2, 9)
    assert "Nothing new to report" in exhausted
    assert "Searched 3 index(es), 4 host(s) and 3 index/sourcetype pair(s)" in exhausted
    assert "no critical condition is active" in exhausted
    assert "New today" not in exhausted


def test_heartbeat_gate_silences_a_repeat_quiet_run_then_posts_once_it_elapses():
    """The fully-quiet case only posts once per HEARTBEAT_HOURS; a real ingest
    anomaly is always CRITICAL and can never be silenced by this gate."""
    _, s0 = step(BASE, None, 0)                 # real post: the day's table
    grown = scale(BASE, "os", 1.6)
    _, s1 = step(grown, s0, 1)                  # real post: the +60% finding
    flat, s2 = step(grown, s1, 2)               # real post: first "flat" finding
    assert "byte-identical" in flat

    soon, s3 = step(grown, s2, 3)
    assert soon == DIGEST.SILENT, \
        "1h after the last real post (well under the 6h ceiling), a repeat quiet run must stay silent"
    assert s3["last_post_iso"] == s2["last_post_iso"], "a silent run must not reset the heartbeat clock"
    assert s3["by_index"]["os"]["vol"] == s2["by_index"]["os"]["vol"], \
        "tracking state keeps updating on a silent run — only delivery is suppressed"

    later_when = at(2) + dt.timedelta(hours=DIGEST.HEARTBEAT_HOURS + 1)
    later, s4 = step(grown, s3, later_when)
    assert "Nothing new to report" in later, "once the heartbeat ceiling elapses, a quiet run posts again"
    assert "heartbeat" in later.lower()
    assert s4["last_post_iso"] != s3["last_post_iso"], "a real heartbeat post must advance the clock"


def test_critical_findings_bypass_the_heartbeat_gate_even_minutes_after_the_last_post():
    dark = {i: h for i, h in BASE.items() if i != "network"}
    first, s0 = step(dark, None, 0)
    assert "INGEST SILENCE: index=network" in first

    # 5 minutes later — nowhere near the 6h heartbeat ceiling — a persisting
    # critical condition must still post, never fall back to the gate above.
    soon_after = at(0) + dt.timedelta(minutes=5)
    second, _ = step(dark, s0, soon_after)
    assert second != DIGEST.SILENT
    assert "INGEST SILENCE: index=network" in second
    assert "Critical — repeats every run" in second


def test_composition_tier_is_reached_when_index_and_host_tiers_are_spent():
    _, s0 = step(BASE, None, 0)
    _, s1 = step(BASE, s0, 1)          # spends the flat finding

    # A new sourcetype inside an index whose total is unchanged: invisible above
    # the composition tier, so only an escalated search can find it.
    mixed = {i: {h: dict(s) for h, s in hosts.items()} for i, hosts in BASE.items()}
    mixed["os"]["host-a"] = {"syslog": 900_000, "auditd": 100_000}
    text, _ = step(mixed, s1, 2)

    assert "sourcetype + composition level" in text
    assert "nothing new at index level, host level" in text
    assert "sourcetype auditd newly present in index=os" in text


def test_the_day_boundary_resets_the_ledger():
    _, s0 = step(BASE, None, 0)
    _, s1 = step(BASE, s0, 23)
    assert "table:2026-07-24" in ledger_keys(s1)

    next_day, s2 = step(BASE, s1, at(0, day=25))
    assert s2["ledger"]["day"] == "2026-07-25"
    assert "table:2026-07-25" in ledger_keys(s2)
    assert not any(k.endswith("2026-07-24") for k in ledger_keys(s2)), \
        "yesterday's keys must not survive the boundary"
    assert "First digest of 2026-07-25" in next_day, "a new day re-presents the baseline table"


def test_old_schema_state_file_is_treated_as_no_baseline_not_a_crash():
    legacy = {"schema": 2, "fingerprint": "deadbeef", "by_index": {"os": {"vol": 5, "hosts": 1}},
              "first_seen_iso": "2026-07-23T17:52:34+00:00", "last_post_date": "2026-07-23"}
    Path(DIGEST.STATE_PATH).write_text(json.dumps(legacy))

    state = DIGEST.load_state()
    assert state == legacy, "the old file must still parse"
    assert DIGEST.baseline_from(state) is None, "an older schema carries no usable baseline"
    assert DIGEST.load_ledger(state, "2026-07-24") == [], "an older schema carries no ledger"

    text, payload = step(BASE, state, 0)
    assert "No prior baseline" in text
    assert "1,200,000" in text, "an unusable baseline must not suppress the real numbers"

    DIGEST.save_state(payload)
    upgraded = DIGEST.load_state()
    assert upgraded["schema"] == DIGEST.STATE_SCHEMA
    assert DIGEST.baseline_from(upgraded)["by_index"]["os"]["vol"] == 1_200_000
    assert not list(Path(STATE_DIR).glob("*.tmp")), "atomic write must leave no temp file"


def test_four_consecutive_hourly_bodies_are_all_different():
    bodies, state, spec = [], None, BASE
    for hour in range(4):
        text, state = step(spec, state, hour)
        bodies.append(text)
        if hour == 0:
            spec = scale(spec, "os", 1.6)
    assert len(set(bodies)) == 4, "no two hourly posts in a day may be identical"


def test_a_fleet_drop_is_critical_but_one_host_leaving_is_routine():
    _, s0 = step(BASE, None, 0)
    one_gone = {i: h for i, h in BASE.items() if i != "firewall"}   # host-d leaves
    text, _ = step(one_gone, s0, 1)
    assert "HOST FLEET DROP" not in text, "one host is not a fleet drop"

    wide = {"os": {f"host-{n}": {"syslog": 1000} for n in range(8)}}
    _, w0 = step(wide, None, 0)
    shrunk = {"os": {f"host-{n}": {"syslog": 1000} for n in range(4)}}
    drop, _ = step(shrunk, w0, 1)
    assert "HOST FLEET DROP: 4 host(s)" in drop
    assert "Critical — repeats every run" in drop


def test_ingest_silence_and_fail_loud_contracts_are_preserved():
    text, _ = step({"os": {"host-a": {"syslog": 10}}}, None, 0)
    assert "INGEST SILENCE" in text and "index=network" in text and "index=firewall" in text

    outage, _ = step([], None, 0)
    assert "SPLUNK INGEST OUTAGE" in outage and "ZERO rows" in outage

    assert ":warning: Splunk digest FAILED:" in TEMPLATE, "fail-loud path must stay"
    assert TEMPLATE.count("call_tool(") == 1 and 'call_tool("splunk_run_query"' in TEMPLATE, \
        "the fact path must be exactly one Splunk tstats call and no LLM"
    assert "ExceptionGroup" in TEMPLATE and "exceptions" in TEMPLATE, \
        "the ExceptionGroup unwrap must stay so a delivered failure names its real cause"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
