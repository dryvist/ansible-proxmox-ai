"""Self-check for the hourly Splunk digest: per-day novelty gate + deltas.

The digest script is a Jinja template whose STDOUT goes verbatim to Slack, so the
contract under test is the *emitted text*.

Two contracts, both enforced here:

1. Per-day novelty — routine information may be presented ONCE per UTC day. A
   later run with nothing genuinely new must escalate its search (host level, then
   sourcetype/composition) rather than re-present it or emit boilerplate. Critical
   conditions are exempt and repeat every run while they hold.
2. Never fabricate — deltas are computed, an absent baseline says so, ingest
   silence is always explicit, and the fact path is one tstats call with no LLM.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_deltas.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
import datetime as dt
import json
import re
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/splunk-digest.py.j2"
TEMPLATE = TEMPLATE_PATH.read_text()

STATE_DIR = tempfile.mkdtemp(prefix="splunk-digest-selfcheck-")
# Stand-ins for the values Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "ENV_PATH": str(Path(STATE_DIR) / "unused.env"),
    "STATE_PATH": str(Path(STATE_DIR) / "splunk-digest.json"),
    "EXPECTED_CONTINUOUS": ["os", "network", "firewall"],
    "STALENESS_MIN": 60,
    "EARLIEST": "-24h",
    "ISSUES_MARKER": "[ISSUES]",
}


def load_digest_module():
    """Render the template's config lines to fixtures and import it as a module."""
    out = []
    for line in TEMPLATE.splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            assert name in FIXTURE_CONFIG, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {FIXTURE_CONFIG[name]!r}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("splunk_digest")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


DIGEST = load_digest_module()


def ledger_keys(state):
    """The finding keys a state file has ledgered, read through the script's own
    accessor rather than off the raw record shape.

    The ledger stores {"k": key, "t": text} records so the heartbeat can restate
    what is still holding instead of only counting it. Assertions that reached
    into ["ledger"]["keys"] directly were pinning that layout, not the contract,
    and broke the moment text was added alongside the key."""
    day = (state or {}).get("ledger", {}).get("day")
    return DIGEST.load_ledger(state, day)

DAY = dt.datetime(2026, 7, 24, 0, 52, tzinfo=dt.timezone.utc)


def at(hour, day=24):
    return DAY.replace(day=day, hour=hour)


def rows(spec, ts):
    """Build tstats rows from {index: {host: {sourcetype: volume}}}.

    Values are strings, exactly as Splunk returns them.
    """
    return [
        {"index": idx, "host": host, "sourcetype": stype,
         "vol": str(vol), "last_time": str(ts)}
        for idx, hosts in spec.items()
        for host, stypes in hosts.items()
        for stype, vol in stypes.items()
    ]


BASE = {
    "os": {"host-a": {"syslog": 1_000_000}, "host-b": {"syslog": 200_000}},
    "network": {"host-c": {"ipfix": 400_000}},
    "firewall": {"host-d": {"pan:traffic": 50_000}},
}


def scale(spec, index, factor):
    """Same shape, one index's volumes multiplied — a real, computable move."""
    out = {i: {h: dict(s) for h, s in hosts.items()} for i, hosts in spec.items()}
    for host in out[index]:
        for stype in out[index][host]:
            out[index][host][stype] = int(out[index][host][stype] * factor)
    return out


def with_stale_host(hour, host, hours):
    """BASE rows for `hour`, with one host's newest event pushed `hours` into the past."""
    now = int(at(hour).timestamp())
    out = rows(BASE, now - 60)
    for row in out:
        if row["host"] == host:
            row["last_time"] = str(now - hours * 3600)
    return out


def step(spec, state, when):
    """One hourly run. `spec` is a nested dict or a raw row list."""
    now = when if isinstance(when, dt.datetime) else at(when)
    results = rows(spec, int(now.timestamp()) - 60) if isinstance(spec, dict) else spec
    return DIGEST.build_digest(results, now, state)


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
    import yaml
    defaults = yaml.safe_load(
        (REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text())
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


def quiet_state():
    """Drive the digest to a genuinely quiet state, the same way
    test_heartbeat_gate_silences_a_repeat_quiet_run_then_posts_once_it_elapses
    does: baseline table, a growth finding, then the "flat" finding. After this
    a further run with identical data has nothing novel to say."""
    grown = scale(BASE, "os", 1.6)
    _, s0 = step(BASE, None, 0)
    _, s1 = step(grown, s0, 1)
    _, s2 = step(grown, s1, 2)
    return grown, s2


def test_the_heartbeat_restates_what_is_still_holding_not_just_a_count():
    """On a quiet day the heartbeat is the ONLY post. A bare "N finding(s) still
    hold" leaves an operator scrolling back with no idea which N — the exact
    unreadability this digest exists to fix. It must restate them."""
    grown, state = quiet_state()
    held = [e["t"] for e in DIGEST.load_ledger_entries(state, "2026-07-24") if e.get("t")]
    assert held, "the quiet state should carry ledgered findings with text"

    beat, _ = step(grown, state, at(2) + dt.timedelta(hours=DIGEST.HEARTBEAT_HOURS + 1))
    assert "Nothing new to report" in beat, beat
    assert "Still holding from earlier today" in beat, beat
    # Each ledgered finding's own text comes back, not a tally of them.
    for text in held[: DIGEST.HEARTBEAT_MAX_RESTATED]:
        assert text in beat, f"heartbeat dropped a still-holding finding: {text!r}"


def test_the_heartbeat_never_truncates_silently():
    """A capped list that does not say it was capped reads as "that is
    everything", which is worse than a long post."""
    grown, state = quiet_state()
    over = DIGEST.HEARTBEAT_MAX_RESTATED + 5
    state["ledger"]["keys"] = [{"k": f"synthetic:{i}", "t": f"finding number {i}"}
                               for i in range(over)] + state["ledger"]["keys"]

    beat, _ = step(grown, state, at(2) + dt.timedelta(hours=DIGEST.HEARTBEAT_HOURS + 1))
    shown = beat.count("finding number ")
    assert shown == DIGEST.HEARTBEAT_MAX_RESTATED, f"restated {shown}, expected the cap"
    assert "more not shown" in beat, beat


def test_an_older_state_file_degrades_the_heartbeat_rather_than_crashing():
    """The ledger used to be a bare list of key strings. A state file written by
    the previous version must not take the digest down; it loses that day's
    restatement detail and nothing else."""
    day = "2026-07-24"
    grown, state = quiet_state()
    # Rewrite the ledger into the OLD shape, keys only.
    state["ledger"]["keys"] = [e["k"] for e in state["ledger"]["keys"]]
    assert DIGEST.load_ledger(state, day) == state["ledger"]["keys"], \
        "the accessor must still read a pre-2026-07-29 ledger"

    beat, _ = step(grown, state, at(2) + dt.timedelta(hours=DIGEST.HEARTBEAT_HOURS + 1))
    assert "Nothing new to report" in beat, beat
    assert "Still holding from earlier today" not in beat, \
        "with no stored text there is nothing to restate"
    # Asserted on the schema-specific sentence, NOT the bare word "predates" —
    # that also appears in the unrelated no-baseline notice, so a looser
    # assertion would pass without the branch under test ever running.
    assert "their text predates" in beat, beat


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
