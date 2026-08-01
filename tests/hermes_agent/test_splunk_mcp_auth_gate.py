"""A delivered-but-dead Splunk MCP credential fails the converge, not the fleet.

The splunk cards' `enabled` gate deliberately excludes the token (defaults:
"queries simply fail to authenticate until then"), which is the right inert
posture for an UNSEEDED token — but it also meant a SEEDED, expired token
401'd every hourly triage run for a week with nothing in the repo or the
converge hinting at it (WS1, 2026-07-31). This pins the converge-time gate
that closes the gap: a hidden probe POSTs an MCP initialize against the
gateway route with the delivered token (accepting 401/403 as well as 200 so
an auth failure surfaces as a real status rather than an opaque no_log module
failure), and a separate visible assert fails the converge on anything but
200 — ordered before the enqueuer reconcile so a converge cannot (re-)enable
cards against a credential it just proved dead.

Runs bare (`python3 tests/hermes_agent/test_splunk_mcp_auth_gate.py`) or under
pytest.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/main.yml").read_text()

PROBE = "Probe the Splunk MCP token against the gateway route"
GATE = "Assert the Splunk MCP token authenticates against the gateway route"


def test_the_gate_exists_and_runs_before_cards_are_reconciled() -> None:
    probe = MAIN_TASKS.find(PROBE)
    gate = MAIN_TASKS.find(GATE)
    reconcile = MAIN_TASKS.find("Reconcile the per-workload Kanban enqueuer crons")
    assert probe != -1, "the Splunk MCP auth probe task is missing"
    assert gate != -1, "the Splunk MCP auth assertion task is missing"
    assert reconcile != -1
    assert probe < gate < reconcile, "probe, then assert, then card reconcile"


def test_the_probe_never_logs_the_bearer_token() -> None:
    block = MAIN_TASKS[MAIN_TASKS.find(PROBE):MAIN_TASKS.find(PROBE) + 1200]
    assert "no_log: true" in block, "the request carries the bearer header"


def test_the_probe_is_read_only_and_does_not_abort_on_a_bad_status() -> None:
    """A stale/expired token must reach the visible assert as a real status
    code, not an opaque no_log module failure — that opacity is what let a
    401ing token run silently for a week (WS1, 2026-07-31)."""
    block = MAIN_TASKS[MAIN_TASKS.find(PROBE):MAIN_TASKS.find(PROBE) + 1200]
    assert "changed_when: false" in block, "the probe must not report changed"
    assert "401" in block and "403" in block, (
        "the probe must accept auth-failure statuses instead of failing opaquely")


def test_the_gate_asserts_on_the_registered_status_only() -> None:
    block = MAIN_TASKS[MAIN_TASKS.find(GATE):MAIN_TASKS.find(GATE) + 600]
    assert "hermes_agent_splunk_mcp_probe.status" in block
    assert "no_log: true" not in block, "the status assert must stay visible"


def test_the_gate_skips_when_the_integration_is_off_unrendered_or_unseeded() -> None:
    """An unseeded token or an unrendered URL is the documented inert posture
    (matches the `mcp_servers.splunk` render gate in config.yaml.j2) and must
    not block a converge; only a DELIVERED credential against a real route
    that fails to authenticate should."""
    for block in (
        MAIN_TASKS[MAIN_TASKS.find(PROBE):MAIN_TASKS.find(PROBE) + 1200],
        MAIN_TASKS[MAIN_TASKS.find(GATE):MAIN_TASKS.find(GATE) + 1200],
    ):
        assert "hermes_agent_splunk_mcp_enabled | bool" in block
        assert "hermes_agent_splunk_mcp_url | length > 0" in block
        assert "hermes_agent_splunk_mcp_token | length > 0" in block


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
