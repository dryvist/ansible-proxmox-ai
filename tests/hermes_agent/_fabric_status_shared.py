"""Shared fixture for the fabric-status self-check.

Same pattern as _splunk_digest_shared.py: render the deployed
fabric-status.py.j2 template with fixed test values and exec it as a real
module, so the tests exercise the same artifact Ansible would actually ship
rather than a hand-copied re-implementation.
"""
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles" / "hermes_agent" / "templates" / "fabric-status.py.j2"
TEMPLATE = TEMPLATE_PATH.read_text()

# Stand-ins for the values Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "{{ ansible_managed | comment }}": "# test render",
    "{{ hermes_agent_hermes_home }}": "/tmp/fabric-status-selfcheck",
    "{{ hermes_agent_slack_noise_channel }}": "C_NOISE",
    "{{ hermes_agent_api_server_port }}": "9001",
    "{{ hermes_agent_dashboard_port }}": "9002",
    "{{ lookup('env', 'PROXMOX_SUBDOMAIN') }}": "example.test",
}

# The exact endpoint set the script must probe -- pinned so a regression that
# reintroduces a guessed/invented address (the 2026-07-31 incident this
# script exists to prevent) fails here, not as a production false alarm.
EXPECTED_URLS = {
    "http://localhost:9001/",
    "http://localhost:9002/",
    "https://hindsight.example.test/",
}


def load_fabric_status_module():
    rendered = TEMPLATE
    for placeholder, value in FIXTURE_CONFIG.items():
        rendered = rendered.replace(placeholder, value)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("fabric_status")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod
