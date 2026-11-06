"""The runtime role does not deploy an event relay."""

from pathlib import Path


ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"


def test_event_relay_is_not_shipped_or_deployed():
    assert not list(ROLE.rglob("*event*relay*"))
    for directory in ("defaults", "tasks", "handlers", "templates"):
        for path in (ROLE / directory).rglob("*"):
            if path.is_file():
                source = path.read_text()
                assert "hermes_agent_event_relay" not in source, path
                assert "hermes-event-relay" not in source, path
                assert "event_relay.yml" not in source, path
