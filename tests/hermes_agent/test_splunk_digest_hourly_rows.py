"""Self-check for how the Splunk digest reads the hourly search's rows.

Covers partial baselines, rows with no buckets, rows with no host field, and the
fleet filters applied to hourly rows. Split from test_splunk_digest_volume.py to
stay under the token budget. _splunk_digest_shared.py holds the loaded template
and fixtures.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_hourly_rows.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
from _splunk_digest_shared import BASE, DIGEST, at, hourly_row, step


def test_a_baseline_shorter_than_24_hours_is_never_down():
    # The search returned two hours before the last complete hour. A median of those
    # two is not the 24-hour median DOWN compares against, so no run may judge it.
    short = [10_000, 10_000, 4_000]

    def rows_at(hour):
        return [hourly_row("langflow", "host-f", short, at(hour))]

    _, s0 = step(BASE, None, 0, hourly=rows_at(0))
    first, s1 = step(BASE, s0, 1, hourly=rows_at(1))
    second, _ = step(BASE, s1, 2, hourly=rows_at(2))
    assert "down" not in first + second, "a partial baseline is not a 24-hour median"


def test_a_new_series_is_not_down_beside_a_full_history_series():
    # host-f has 12 hours of history, then a drop. It must not borrow the 24-hour
    # median of host-a's full history: its own history does not reach the window.
    def rows_at(hour, last):
        return [hourly_row("langflow", "host-f", [10_000] * 12 + [last], at(hour)),
                hourly_row("os", "host-a", [6_000] * 25, at(hour))]

    _, s0 = step(BASE, None, 0, hourly=rows_at(0, 10_000))
    first, s1 = step(BASE, s0, 1, hourly=rows_at(1, 2_000))
    second, _ = step(BASE, s1, 2, hourly=rows_at(2, 2_000))
    assert "down" not in first + second, "a series without 24 hours of history has no baseline"


def _relay(hour, last, host_field=True):
    # One hourly row for the hostless index `relay`: a steady day, then `last`.
    row = hourly_row("relay", None, [6_000] * 24 + [last], at(hour))
    if not host_field:
        del row["host"]
    return [row]


def test_hostless_rows_count_toward_the_index_and_sourcetype_totals():
    # Rows with no host field, or a null one, still carry volume. They are judged in the
    # index and sourcetype totals; only the per-host breakdown leaves them out.
    for host_field in (True, False):
        _, s0 = step(BASE, None, 0, hourly=_relay(0, 6_000, host_field))
        _, s1 = step(BASE, s0, 1, hourly=_relay(1, 2_000, host_field))
        text, _ = step(BASE, s1, 2, hourly=_relay(2, 2_000, host_field))
        assert "index=relay down" in text, f"host field present={host_field}: index total is judged"
        assert "sourcetype syslog in index=relay down" in text, f"host field present={host_field}: sourcetype"
        assert "host None" not in text, f"host field present={host_field}: no per-host finding"


def test_an_hourly_row_without_buckets_does_not_fail_the_run():
    # The "hours" value may be empty, null or absent. None of them may fail the run.
    empty_rows = [
        {"index": "os", "host": "host-a", "sourcetype": "syslog", "hours": ""},
        {"index": "os", "host": "host-a", "sourcetype": "syslog", "hours": None},
        {"index": "os", "host": "host-a", "sourcetype": "syslog"},
    ]
    for row in empty_rows:
        _, s0 = step(BASE, None, 0, hourly=[row])
        text, _ = step(BASE, s0, 1, hourly=[row])
        assert text == DIGEST.SILENT, f"row {row}: no buckets is no finding"


def test_test_senders_and_roster_hosts_are_never_judged_from_hourly_rows():
    # The e2e senders and the roster-only host drop to zero in the hourly search. The
    # fleet filters apply to hourly rows as well as to the tstats rows.
    senders = {"dns": {"host-h": {"technitium:dnsquery": 500}},
               "main": {"probe-a": {"e2e:hec:test": 500}}}
    roster = {"ansible": {"roster-a": {"ansible:converge:roster": 500}}}
    spec = {**BASE, **senders, **roster}
    stypes = [("dns", "host-h", "technitium:dnsquery"),
              ("main", "probe-a", "e2e:hec:test"),
              ("ansible", "roster-a", "ansible:converge:roster")]

    def rows_at(hour, last):
        return [hourly_row(idx, host, [500] * 24 + [last], at(hour), sourcetype=stype)
                for idx, host, stype in stypes]

    _, s0 = step(spec, None, 0, hourly=rows_at(0, 500))
    first, s1 = step(spec, s0, 1, hourly=rows_at(1, 0))
    second, _ = step(spec, s1, 2, hourly=rows_at(2, 0))
    assert "down" not in first + second, "test senders and roster-only hosts are never down"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
