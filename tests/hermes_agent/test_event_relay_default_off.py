"""The event relay ships off. Its queue inputs come from the secret store, so an
unset input must not fail converge while the default is in force."""
from pathlib import Path

from _role_files import role_defaults, role_tasks

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)


def test_event_relay_defaults_off():
    assert DEFAULTS["hermes_agent_event_relay_enabled"] is False


def test_every_event_relay_task_waits_on_the_flag():
    tasks = role_tasks(ROLE, "event_relay.yml")
    assert tasks, "event_relay.yml resolved to no tasks"
    for task in tasks:
        assert "hermes_agent_event_relay_enabled" in str(task.get("when", "")), task.get("name")
