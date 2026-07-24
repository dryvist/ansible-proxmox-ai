"""Self-check for the hourly Splunk digest delta logic.

The digest script is a Jinja template whose STDOUT goes verbatim to Slack, so the
contract under test is the *emitted text*: every hourly post must carry real
per-index volumes AND their change since the previous run — never a recycled
"No change" line.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_deltas.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
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
}

NOW = 1_784_918_400  # fixed epoch; last_time is derived from it so nothing is stale
FRESH = NOW - 60


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


def rows(spec, last_time=FRESH):
    """Build tstats rows from {index: {host: volume}} — values as Splunk returns them."""
    return [
        {"index": idx, "host": host, "vol": str(vol), "last_time": str(last_time)}
        for idx, hosts in spec.items()
        for host, vol in hosts.items()
    ]


BASE = {
    "os": {"host-a": 1_000_000, "host-b": 200_000},
    "network": {"host-c": 400_000},
    "firewall": {"host-d": 50_000},
}


def run(results, prev):
    return DIGEST.build_digest(results, NOW, prev)


def test_first_run_has_no_baseline_but_still_reports_real_numbers():
    text, _, snapshot = run(rows(BASE), None)
    assert "no prior baseline" in text.lower()
    assert "no baseline" in text, "delta cells must say why a delta is missing"
    assert "1,200,000" in text, "per-index volume must be present on the very first run"
    assert "Δ unavailable" in text
    assert snapshot["by_index"]["os"] == {"vol": 1_200_000, "hosts": 2}
    assert snapshot["hosts"] == ["host-a", "host-b", "host-c", "host-d"]


def test_changed_run_reports_signed_deltas_and_movers():
    prev = {
        "by_index": {"os": {"vol": 1_200_000, "hosts": 2}, "network": {"vol": 400_000, "hosts": 1},
                     "firewall": {"vol": 50_000, "hosts": 1}, "dns": {"vol": 900, "hosts": 1}},
        "hosts": ["host-a", "host-b", "host-c", "host-d", "host-e"],
        "host_detail": True,
        "captured_iso": "2026-07-24T17:52:00+00:00",
    }
    changed = {
        "os": {"host-a": 1_012_004, "host-b": 200_000},   # +12,004  (+1%, not a mover)
        "network": {"host-c": 100_000},                    # -300,000 (-75%, mover)
        "firewall": {"host-d": 50_000},                    # 0
        "proxy": {"host-f": 7_000},                        # NEW index, NEW host
    }
    text, _, _ = run(rows(changed), prev)

    assert "Δ vs 17:52 UTC" in text, "the comparison window must be stated, not implied"
    assert "+12,004" in text
    assert "-300,000" in text
    assert re.search(r"firewall\s+50,000\s+0\b", text), "zero change must be explicit, not omitted"
    assert "NEW" in text and "proxy" in text
    assert "Movers" in text and "network -75%" in text
    assert "dns STOPPED reporting" in text and "900" in text
    assert "1 host(s) stopped logging" in text and "host-e" in text
    assert "1 host(s) started logging" in text and "host-f" in text
    assert "FLAT" not in text


def test_flat_run_states_it_is_flat_and_still_carries_every_number():
    _, _, snapshot = run(rows(BASE), None)
    prev = dict(snapshot, captured_iso="2026-07-24T17:52:00+00:00")
    text, _, _ = run(rows(BASE), prev)

    assert "FLAT" in text, "an all-flat hour must say so explicitly"
    assert "1,200,000" in text and "400,000" in text and "50,000" in text, \
        "a flat post must still carry the real volumes, not a bare 'No change'"
    assert "hosts: 4 (flat)" in text
    assert "No change —" not in text, "the recycled boilerplate branch must stay deleted"


def test_old_schema_state_file_is_treated_as_no_baseline_not_a_crash():
    legacy = {"fingerprint": "deadbeef", "first_seen_iso": "2026-07-24T17:52:34+00:00",
              "last_post_date": "2026-07-24"}
    Path(DIGEST.STATE_PATH).write_text(json.dumps(legacy))

    state = DIGEST.load_state()
    assert state == legacy, "the old file must still parse"
    assert DIGEST.baseline_from(state) is None, "schema 1 carries no deltas -> no baseline"

    text, fingerprint, snapshot = run(rows(BASE), DIGEST.baseline_from(state))
    assert "no prior baseline" in text.lower()
    assert "1,200,000" in text

    # and the round-trip upgrades the file in place, atomically
    DIGEST.save_state(fingerprint, legacy["first_seen_iso"], "2026-07-24", snapshot,
                      "2026-07-24T18:52:00+00:00")
    upgraded = DIGEST.load_state()
    assert upgraded["schema"] == DIGEST.STATE_SCHEMA
    assert DIGEST.baseline_from(upgraded)["by_index"]["os"]["vol"] == 1_200_000
    assert not list(Path(STATE_DIR).glob("*.tmp")), "atomic write must leave no temp file"


def test_three_consecutive_hourly_bodies_are_all_different():
    first, _, snap1 = run(rows(BASE), None)
    prev1 = dict(snap1, captured_iso="2026-07-24T17:52:00+00:00")
    grown = {"os": {"host-a": 1_050_000, "host-b": 200_000}, "network": {"host-c": 400_000},
             "firewall": {"host-d": 50_000}}
    second, _, snap2 = run(rows(grown), prev1)
    prev2 = dict(snap2, captured_iso="2026-07-24T18:52:00+00:00")
    third, _, _ = run(rows(grown), prev2)

    assert len({first, second, third}) == 3, "no two consecutive hourly posts may be identical"
    for body in (first, second, third):
        assert "1,0" in body or "1,2" in body, "every post carries real volumes"


def test_ingest_silence_and_fail_loud_contracts_are_preserved():
    text, _, _ = run(rows({"os": {"host-a": 10}}), None)
    assert "INGEST SILENCE" in text and "index=network" in text and "index=firewall" in text

    outage, _, _ = run([], None)
    assert "SPLUNK INGEST OUTAGE" in outage and "ZERO rows" in outage

    assert ":warning: Splunk digest FAILED:" in TEMPLATE, "fail-loud path must stay"
    assert TEMPLATE.count("call_tool(") == 1 and 'call_tool("splunk_run_query"' in TEMPLATE, \
        "the fact path must be exactly one Splunk tstats call and no LLM"


def test_stability_line_never_claims_a_change_against_nothing():
    import datetime as dt

    now = dt.datetime(2026, 7, 24, 18, 52, tzinfo=dt.timezone.utc)
    fresh, _ = DIGEST.stability_line(None, "fp1", now, "2026-07-24")
    assert "baseline established" in fresh and "CHANGED" not in fresh

    changed, _ = DIGEST.stability_line({"fingerprint": "fp0"}, "fp1", now, "2026-07-24")
    assert "CHANGED" in changed

    held, first_seen = DIGEST.stability_line(
        {"fingerprint": "fp1", "first_seen_iso": "2026-07-22T00:00:00+00:00"}, "fp1", now, "2026-07-24")
    assert "Day 3 since 2026-07-22" in held
    assert first_seen == "2026-07-22T00:00:00+00:00", "stable state must keep its original first_seen"


def test_host_field_absence_is_reported_as_unavailable_not_zero():
    hostless = [{"index": "os", "vol": "500", "last_time": str(FRESH)}]
    text, _, snapshot = run(hostless, None)
    assert "hosts: unavailable" in text
    assert snapshot["host_detail"] is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
