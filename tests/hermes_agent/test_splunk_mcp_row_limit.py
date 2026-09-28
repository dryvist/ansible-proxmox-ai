"""Every script-fed Splunk job asks splunk_run_query for a row limit the tool accepts.

The tool declares `row_limit` with a maximum of 1000 and rejects anything above it
with is_error=true, so an out-of-range value fails every run of the job. Below the
default of 100 rows the response is silently truncated instead.

Runs bare (`python3 tests/hermes_agent/test_splunk_mcp_row_limit.py`) or under pytest.
"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "templates"
SCRIPTS = ("splunk-digest.py.j2", "splunk-triage.py.j2", "splunk-wired-trajectory.py.j2")
TOOL_MAXIMUM = 1000


def test_row_limit_is_within_the_tool_maximum_and_is_passed():
    for name in SCRIPTS:
        text = (TEMPLATES / name).read_text()
        match = re.search(r"^ROW_LIMIT = (\d+)", text, re.M)
        assert match, f"{name}: no ROW_LIMIT constant"
        assert 100 < int(match.group(1)) <= TOOL_MAXIMUM, (
            f"{name}: ROW_LIMIT {match.group(1)} outside (100, {TOOL_MAXIMUM}]")
        assert '"row_limit": ROW_LIMIT' in text, f"{name}: ROW_LIMIT is not passed to the tool"
        assert '"earliest_time": EARLIEST' in text, f"{name}: window not passed as earliest_time"


if __name__ == "__main__":
    test_row_limit_is_within_the_tool_maximum_and_is_passed()
    print("ok  test_row_limit_is_within_the_tool_maximum_and_is_passed")
