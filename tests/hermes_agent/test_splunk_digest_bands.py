"""Self-check for the hourly Splunk digest's host-field and schedule-vs-heartbeat contracts.

Split from test_splunk_digest_deltas.py to stay under the token budget — see
_splunk_digest_shared.py for the loaded template/state fixtures and
test_splunk_digest_novelty.py for the per-day novelty/delta contract this
leaves behind.

Runs bare (`python3 tests/hermes_agent/test_splunk_digest_bands.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
from _role_files import role_defaults
from _splunk_digest_shared import REPO_ROOT, TEMPLATE, at, step


def test_host_field_absence_is_kept_in_state_without_inventing_zero_hosts():
    hostless = [{"index": "os", "vol": "500", "last_time": str(int(at(0).timestamp()) - 60)}]
    text, state = step(hostless, None, 0)
    assert "hosts: 0" not in text, "a missing host field must not invent a host count"
    assert state["host_detail"] is False and state["st_detail"] is False


def test_quiet_splunk_runs_have_no_heartbeat():
    defaults = role_defaults(REPO_ROOT / "roles" / "hermes_agent")
    assert "HEARTBEAT_HOURS" not in TEMPLATE, "quiet runs must have no heartbeat path"
    schedule = defaults["hermes_agent_splunk_status_digest_cron_schedule"]

    minute, hours = schedule.split()[0], schedule.split()[1]
    assert hours != "*", (
        "schedule is back to 24/7; if that is intended, delete this check "
        "rather than loosening it")
    start, end = (int(part) for part in hours.split("-"))
    assert 0 <= int(minute) <= 59 and 0 <= start < end <= 23, schedule


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
