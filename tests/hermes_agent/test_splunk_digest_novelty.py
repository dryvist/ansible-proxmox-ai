"""Self-check for the hourly Splunk digest's finding-only delivery + deltas.

Split from test_splunk_digest_deltas.py to stay under the token budget — see
_splunk_digest_shared.py for the loaded template/state fixtures and
test_splunk_digest_heartbeat.py for the heartbeat-restatement and
persistent-failure contracts this leaves behind.

Two contracts, both enforced here:

1. Finding-only delivery — unchanged results stay silent. A later run with a
   new lower-level finding escalates its search (host, then sourcetype and
   composition); an active finding does not repeat until it clears.
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
    hourly_row,
    ledger_keys,
    scale,
    step,
    with_new_index,
    with_stale_host,
)


def test_first_run_records_a_baseline_without_a_status_post():
    text, state = step(BASE, None, 0)

    assert text == DIGEST.SILENT
    assert state["ledger"]["day"] == "2026-07-24"
    assert ledger_keys(state) == []
    assert state["by_index"]["os"] == {
        "vol": 1_200_000, "hosts": 2,
        "host_vol": {"host-a": 1_000_000, "host-b": 200_000},
        "st_vol": {"syslog": 1_200_000},
    }


def test_a_routine_finding_already_posted_today_is_suppressed():
    _, s0 = step(BASE, None, 0)
    grown = with_new_index(BASE)              # a routine index-level change

    second, s1 = step(grown, s0, 1)
    assert "index=audit newly reporting" in second, "a novel routine change must be posted"
    assert "First digest of" not in second, "the daily table is routine — once per day only"
    key = next(k for k in ledger_keys(s1) if k.startswith("idx+:audit"))

    # The same change an hour later against the new baseline: same identity.
    third, s2 = step(grown, s1, 2)
    assert ledger_keys(s2).count(key) == 1, "a repeat must not be re-ledgered"
    assert "index=audit newly reporting" not in third, \
        "a routine finding already presented today must be suppressed"


def test_a_critical_condition_posts_once_until_a_successful_run_clears_it():
    dark = {i: h for i, h in BASE.items() if i != "network"}
    first, s0 = step(dark, None, 0)
    second, s1 = step(dark, s0, 1)
    third, _ = step(dark, s1, 2)

    assert "INGEST SILENCE: index=network" in first
    assert second == third == DIGEST.SILENT
    assert any(k.startswith("crit:") for k in ledger_keys(s1))


def test_an_exhausted_routine_search_escalates_before_it_gives_up():
    _, s0 = step(BASE, None, 0)

    # Identical data is not a finding and stays silent.
    flat, s1 = step(BASE, s0, 1)
    assert flat == DIGEST.SILENT

    # Still identical, but one host's newest event is now 3h old -> host tier.
    escalated, s2 = step(with_stale_host(2, "host-b", hours=3), s1, 2)
    assert "host level" in escalated and "nothing new at index level" in escalated
    assert "host host-b" in escalated and "3.0 h old" in escalated

    # Once the stale-host finding has been reported, an unchanged follow-up is
    # silent regardless of elapsed time.
    exhausted, _ = step(with_stale_host(9, "host-b", hours=3), s2, 9)
    assert exhausted == DIGEST.SILENT


def test_fully_quiet_runs_remain_silent_without_a_heartbeat():
    """Quiet runs stay silent regardless of elapsed time; findings still post."""
    baseline, s0 = step(BASE, None, 0)
    assert baseline == DIGEST.SILENT
    grown = with_new_index(BASE)
    _, s1 = step(grown, s0, 1)                  # real post: the new index
    flat, s2 = step(grown, s1, 2)
    assert flat == DIGEST.SILENT

    soon, s3 = step(grown, s2, 3)
    assert soon == DIGEST.SILENT, \
        "an unchanged finding must stay silent"
    assert s3["last_post_iso"] == s2["last_post_iso"], "a silent run must not advance delivery state"
    assert s3["by_index"]["os"]["vol"] == s2["by_index"]["os"]["vol"], \
        "tracking state keeps updating on a silent run — only delivery is suppressed"

    later_when = at(2) + dt.timedelta(days=1)
    later, s4 = step(grown, s3, later_when)
    assert later == DIGEST.SILENT, "a healthy no-change run must not post a baseline or heartbeat"
    assert s4["last_post_iso"] == s3["last_post_iso"]


def test_critical_findings_post_when_new_and_stay_silent_while_unchanged():
    dark = {i: h for i, h in BASE.items() if i != "network"}
    first, s0 = step(dark, None, 0)
    assert "INGEST SILENCE: index=network" in first

    # A persisting critical condition is already known and does not repeat.
    soon_after = at(0) + dt.timedelta(minutes=5)
    second, _ = step(dark, s0, soon_after)
    assert second == DIGEST.SILENT


def test_composition_tier_is_reached_when_index_and_host_tiers_are_spent():
    _, s0 = step(BASE, None, 0)
    _, s1 = step(BASE, s0, 1)

    # A new sourcetype inside an index whose total is unchanged: invisible above
    # the composition tier, so only an escalated search can find it.
    mixed = {i: {h: dict(s) for h, s in hosts.items()} for i, hosts in BASE.items()}
    mixed["os"]["host-a"] = {"syslog": 900_000, "auditd": 100_000}
    text, _ = step(mixed, s1, 2)

    assert "sourcetype + composition level" in text
    assert "nothing new at index level, host level" in text
    assert "sourcetype auditd newly present in index=os" in text


def test_the_day_boundary_does_not_repeat_a_still_active_finding():
    _, s0 = step(BASE, None, 0)
    moved = with_new_index(BASE)
    first, s1 = step(moved, s0, 1)
    assert "index=audit newly reporting" in first

    next_day, s2 = step(moved, s1, at(0, day=25))
    assert s2["ledger"]["day"] == "2026-07-25"
    assert next_day == DIGEST.SILENT


def test_old_schema_state_file_is_treated_as_no_baseline_not_a_crash():
    legacy = {"schema": 2, "fingerprint": "deadbeef", "by_index": {"os": {"vol": 5, "hosts": 1}},
              "first_seen_iso": "2026-07-23T17:52:34+00:00", "last_post_date": "2026-07-23"}
    Path(DIGEST.STATE_PATH).write_text(json.dumps(legacy))

    state = DIGEST.load_state()
    assert state == legacy, "the old file must still parse"
    assert DIGEST.baseline_from(state) is None, "an older schema carries no usable baseline"
    assert DIGEST.load_ledger(state, "2026-07-24") == [], "an older schema carries no ledger"

    text, payload = step(BASE, state, 0)
    assert text == DIGEST.SILENT, "an unusable baseline initializes state without a status post"

    DIGEST.save_state(payload)
    upgraded = DIGEST.load_state()
    assert upgraded["schema"] == DIGEST.STATE_SCHEMA
    assert DIGEST.baseline_from(upgraded)["by_index"]["os"]["vol"] == 1_200_000
    assert not list(Path(STATE_DIR).glob("*.tmp")), "atomic write must leave no temp file"


def test_unchanged_findings_are_silent_across_consecutive_runs():
    baseline, state = step(BASE, None, 0)
    assert baseline == DIGEST.SILENT
    moved = with_new_index(BASE)
    first, state = step(moved, state, 1)
    assert first != DIGEST.SILENT
    for hour in (2, 3, 4):
        text, state = step(moved, state, hour)
        assert text == DIGEST.SILENT


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
    assert "New critical findings" in drop


def test_a_sparse_index_aging_out_of_the_window_is_not_critical():
    # A series of a few events a day leaves the 24h window on its own; it did not
    # go quiet. A series that carried real volume going to zero still does.
    sparse = {**BASE, "clickhouse": {"host-e": {"syslog": 2}}}
    _, s0 = step(sparse, None, 0)
    text, _ = step(BASE, s0, 1)
    assert "went to ZERO" not in text, "a 2-event index aging out is not an outage"

    busy = {**BASE, "langflow": {"host-f": {"syslog": 5000}}}
    _, b0 = step(busy, None, 0)
    text, _ = step(BASE, b0, 1)
    assert "index=langflow went to ZERO" in text
    assert "New critical findings" in text


def test_a_test_sender_leaving_is_not_a_fleet_drop():
    # The e2e suites write from a named host and from e2e:* sourcetypes on their
    # own schedule. Three of them going quiet at once is not a fleet drop.
    senders = {"dns": {"ansible-e2e-host": {"technitium:dnsquery": 500}},
               "main": {"probe-a": {"e2e:hec:test": 500}, "probe-b": {"e2e:hec:test": 500}}}
    _, s0 = step({**BASE, **senders}, None, 0)
    text, _ = step(BASE, s0, 1)
    assert text == DIGEST.SILENT, "test senders are not part of the fleet"


def test_a_roster_only_host_leaving_is_not_a_fleet_drop():
    # Converge roster rows say a host is in inventory, not that it logs.
    roster = {"ansible": {"roster-a": {"ansible:converge:roster": 4},
                          "roster-b": {"ansible:converge:roster": 4},
                          "roster-c": {"ansible:converge:roster": 4}}}
    _, s0 = step({**BASE, **roster}, None, 0)
    text, _ = step(BASE, s0, 1)
    assert text == DIGEST.SILENT, "roster-only hosts are sparse, not a fleet drop"


def test_a_one_hour_burst_then_normal_traffic_is_not_down():
    # The llm-router-3 shape: one hour at 55k events inside the 24 hours before the
    # last complete hour, then 5-9k an hour. The burst is an outlier the median
    # ignores, not a decline, so neither run may report the host or index down.
    spec = {**BASE, "llm": {"llm-router-3": {"litellm:proxy": 6_000}}}
    counts = [5_000 + (i * 997) % 4_001 for i in range(24)]
    counts[10] = 55_501
    rows_at = lambda hour: [hourly_row("llm", "llm-router-3", counts + [6_000], at(hour))]
    _, state = step(spec, None, 0, hourly=rows_at(0))
    for hour in (1, 2):
        text, state = step(spec, state, hour, hourly=rows_at(hour))
        assert "down" not in text, f"hour {hour}: a burst that ages out is not a decline"


def test_a_series_below_half_its_hourly_median_is_down_only_after_two_runs():
    steady = [6_000] * 24

    def rows_at(hour, low):
        # host-a drops to `low`; host-b holds, so the index total is not down.
        return [hourly_row("os", "host-a", steady + [low], at(hour)),
                hourly_row("os", "host-b", steady + [6_000], at(hour))]

    _, s0 = step(BASE, None, 0, hourly=rows_at(0, 6_000))
    first, s1 = step(BASE, s0, 1, hourly=rows_at(1, 2_000))
    assert "down" not in first, "one low hour is not down"
    second, s2 = step(BASE, s1, 2, hourly=rows_at(2, 2_000))
    assert "host host-a in index=os down" in second, "two low hours in a row are down"
    third, _ = step(BASE, s2, 3, hourly=rows_at(3, 2_000))
    assert third == DIGEST.SILENT, "a down finding already presented is not repeated"


def test_a_series_under_the_volume_floor_is_never_down():
    quiet = [5] * 25
    _, s0 = step(BASE, None, 0, hourly=[hourly_row("firewall", "host-d", quiet, at(0))])
    dropped = [5] * 24 + [0]
    first, s1 = step(BASE, s0, 1, hourly=[hourly_row("firewall", "host-d", dropped, at(1))])
    second, _ = step(BASE, s1, 2, hourly=[hourly_row("firewall", "host-d", dropped, at(2))])
    assert "down" not in first + second, "a handful of events an hour is not a volume to lose"


def test_a_one_hour_burst_that_ages_out_of_a_sourcetype_is_not_down():
    # The llm-router-3 burst seen by one sourcetype: 55k in one hour, then 5-6k an
    # hour. The rolling 24h total fell by the burst when it left the window; the
    # hourly median ignores it, so no run may report the sourcetype down.
    timeline = [5_000 + (i * 997) % 1_000 for i in range(60)]
    timeline[30] = 55_501
    state = None
    for start, hour in ((29, 0), (30, 1), (31, 2)):     # the burst leaves the window at hour 2
        window = timeline[start:start + 25]
        spec = {**BASE, "llm": {"llm-router-3": {"litellm:proxy": sum(window[:24])}}}
        hourly = [hourly_row("llm", "llm-router-3", window, at(hour), sourcetype="litellm:proxy")]
        text, state = step(spec, state, hour, hourly=hourly)
        assert "down" not in text, f"hour {hour}: a burst that ages out is not a decline"


def test_a_sourcetype_that_stops_is_down_after_two_runs():
    # A sourcetype that goes silent is a real loss. It is reported on the second low
    # run, in the sourcetype tier, and only once.
    spec = {**BASE, "firewall": {"host-d": {"pan:traffic": 2_000, "pan:threat": 20_000}}}

    def rows_at(hour, traffic):
        return [hourly_row("firewall", "host-d", [2_000] * 24 + [traffic], at(hour), sourcetype="pan:traffic"),
                hourly_row("firewall", "host-d", [20_000] * 25, at(hour), sourcetype="pan:threat")]

    _, s0 = step(spec, None, 0, hourly=rows_at(0, 2_000))
    first, s1 = step(spec, s0, 1, hourly=rows_at(1, 0))
    assert "down" not in first, "one silent hour is not down"
    second, s2 = step(spec, s1, 2, hourly=rows_at(2, 0))
    assert "sourcetype pan:traffic in index=firewall down" in second
    assert "sourcetype + composition level" in second
    third, _ = step(spec, s2, 3, hourly=rows_at(3, 0))
    assert third == DIGEST.SILENT, "a down finding already presented is not repeated"


def test_a_rolling_total_rise_is_not_an_index_finding():
    _, s0 = step(BASE, None, 0)
    text, _ = step(scale(BASE, "os", 1.45), s0, 1)
    assert "index=os volume" not in text, "index volume is judged on hourly counts only"


def test_ingest_silence_and_fail_loud_contracts_are_preserved():
    text, _ = step({"os": {"host-a": {"syslog": 10}}}, None, 0)
    assert "INGEST SILENCE" in text and "index=network" in text and "index=firewall" in text

    outage, _ = step([], None, 0)
    assert "SPLUNK INGEST OUTAGE" in outage and "ZERO rows" in outage

    assert ":warning: Splunk digest FAILED:" in TEMPLATE, "fail-loud path must stay"
    assert TEMPLATE.count('call_tool("splunk_run_query"') == 1, \
        "both bounded searches must use the one structured MCP call site"
    assert "run_tstats(url, token)" in TEMPLATE and "run_backup_outcomes(url, token)" in TEMPLATE, \
        "the digest must keep the tstats and backup outcome searches explicit"
    assert "ExceptionGroup" in TEMPLATE and "exceptions" in TEMPLATE, \
        "the ExceptionGroup unwrap must stay so a delivered failure names its real cause"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
