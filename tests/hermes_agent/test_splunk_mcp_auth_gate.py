"""A delivered-but-dead Splunk MCP credential fails the converge, not the fleet.

The splunk cards' `enabled` gate deliberately excludes the token (defaults:
"queries simply fail to authenticate until then"), which is the right inert
posture for an UNSEEDED token — but it also meant a SEEDED, expired token
401'd every hourly triage run for a week with nothing in the repo or the
converge hinting at it (WS1, 2026-07-31). This pins the converge-time gate
that closes the gap: an MCP initialize against the gateway route with the
delivered token, failing the converge on anything but 200, ordered before the
enqueuer reconcile so a converge cannot (re-)enable cards against a credential
it just proved dead.

Runs bare (`python3 tests/hermes_agent/test_splunk_mcp_auth_gate.py`) or under
pytest.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/main.yml").read_text()

GATE = "Assert the Splunk MCP token authenticates against the gateway route"


def test_the_gate_exists_and_runs_before_cards_are_reconciled() -> None:
    gate = MAIN_TASKS.find(GATE)
    reconcile = MAIN_TASKS.find("Reconcile the per-workload Kanban enqueuer crons")
    assert gate != -1, "the Splunk MCP auth assertion task is missing"
    assert reconcile != -1
    assert gate < reconcile, "the assertion must run before cards are reconciled"


def test_the_gate_never_logs_the_bearer_token() -> None:
    block = MAIN_TASKS[MAIN_TASKS.find(GATE):MAIN_TASKS.find(GATE) + 1200]
    assert "no_log: true" in block, "the request carries the bearer header"


def test_the_gate_skips_when_the_integration_is_off_or_unseeded() -> None:
    """An unseeded token is the documented inert posture and must not block a
    converge; only a DELIVERED credential that fails to authenticate should."""
    block = MAIN_TASKS[MAIN_TASKS.find(GATE):MAIN_TASKS.find(GATE) + 1200]
    assert "hermes_agent_splunk_mcp_enabled | bool" in block
    assert "hermes_agent_splunk_mcp_token | length > 0" in block


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
