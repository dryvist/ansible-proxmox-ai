"""The runtime role does not deploy an event relay."""

from pathlib import Path

import pytest
import yaml
from jinja2 import Environment


ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"


def test_event_relay_is_not_shipped_or_deployed():
    for directory in ("files", "templates"):
        assert not list((ROLE / directory).glob("*event*relay*"))
    for directory in ("defaults", "tasks", "handlers"):
        for path in (ROLE / directory).rglob("*"):
            if path.is_file() and "cleanup" not in path.name:
                source = path.read_text()
                assert "hermes_agent_event_relay_enabled" not in source, path
                assert "hermes-event-relay" not in source, path
                assert "event_relay.yml" not in source, path


def test_relay_cleanup_is_default_enabled_and_gated():
    defaults = yaml.safe_load((ROLE / "defaults/main/53-event-relay-cleanup.yml").read_text())
    assert defaults == {"hermes_agent_event_relay_cleanup": "{{ hermes_event_relay_cleanup | default(true) }}"}
    expression = Environment().compile_expression("hermes_event_relay_cleanup | default(true)")
    assert expression() is True
    assert expression(hermes_event_relay_cleanup=False) is False
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    cleanup = next(task for task in tasks if task.get("ansible.builtin.import_tasks") == "event_relay_cleanup.yml")
    assert cleanup["when"] == "hermes_agent_event_relay_cleanup | bool"


@pytest.mark.parametrize("services,stops", [({}, False), ({"hermes-event-relay.service": {}}, True)])
def test_relay_cleanup_handles_present_and_absent_service(services, stops):
    tasks = yaml.safe_load((ROLE / "tasks/event_relay_cleanup.yml").read_text())
    assert "ansible.builtin.service_facts" in tasks[0]
    stop = tasks[1]
    assert stop["ansible.builtin.systemd_service"] == {
        "name": "hermes-event-relay.service", "state": "stopped", "enabled": False,
    }
    assert Environment().compile_expression(stop["when"])(ansible_facts={"services": services}) == stops
    remove = tasks[2]
    assert remove["ansible.builtin.file"] == {"path": "{{ item }}", "state": "absent"}
    assert set(remove["loop"]) == {
        "/etc/systemd/system/hermes-event-relay.service",
        "/usr/local/libexec/hermes-event-relay.py",
        "/usr/local/libexec/hermes_event_relay_dify.py",
        "/etc/hermes-event-relay.env",
        "/opt/hermes-event-relay",
    }
    handler = next(
        task for task in yaml.safe_load((ROLE / "handlers/main.yml").read_text())
        if task["name"] == remove["notify"]
    )
    assert handler["ansible.builtin.systemd_service"] == {"daemon_reload": True}
