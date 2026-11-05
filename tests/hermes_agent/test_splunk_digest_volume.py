"""Self-check for the hourly Splunk digest's fleet and volume judgments.

Split from test_splunk_digest_novelty.py to stay under the token budget. The
fleet tests cover hosts leaving the fleet. The volume tests judge each index and
sourcetype on hourly counts, not rolling totals. _splunk_digest_shared.py holds
the loaded template and state fixtures.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_volume.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
from _splunk_digest_shared import (
    BASE,
    DIGEST,
    at,
    hourly_row,
    scale,
    step,
)


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
