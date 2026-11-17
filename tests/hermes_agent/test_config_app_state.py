"""A converge renders back the keys Hermes writes into its own config.yaml.

Runs tasks/config_app_state.yml with Ansible against a fixture live config,
then renders templates/config-app-state.yaml.j2 and the monitoring macro."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/hermes_agent"
LIVE = {
    "_config_version": 52,
    "_left_core_installed": ["homeassistant", "langfuse"],
    "known_plugin_toolsets": {"cron": ["homeassistant"]},
    "model": {"default": "role-owned"},
    "mcp_servers": {"docs": {"url": "https://mcp.example.test/docs"}},
    "monitoring": {"install_id": "8dad40bd-0000-4000-8000-000000000000"},
}


def _run(tmp_path, live):
    config = tmp_path / "config.yaml"
    if live is not None:
        config.write_text(yaml.safe_dump(live))
    out = tmp_path / "rendered.yaml"
    templates = tmp_path / "templates"
    templates.mkdir()
    for name in ("config-monitoring.yaml.j2", "config-app-state.yaml.j2"):
        shutil.copy2(ROLE / "templates" / name, templates / name)
    (templates / "wrapper.j2").write_text(
        "{% import 'config-monitoring.yaml.j2' as m %}"
        "{{ m.gateway_health_metrics('https://otel.example.test', 60, hermes_agent_config_install_id) }}"
        "{% include 'config-app-state.yaml.j2' %}")
    defaults = yaml.safe_load((ROLE / "defaults/main/12-config-app-state.yml").read_text())
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {**defaults, "hermes_agent_config_file": str(config)},
        "tasks": [
            {"ansible.builtin.import_tasks": str(ROLE / "tasks/config_app_state.yml")},
            {"ansible.builtin.template": {"src": "wrapper.j2", "dest": str(out)}},
        ],
    }], sort_keys=False))
    env = {**os.environ, "ANSIBLE_CONFIG": str(tmp_path / "ansible.cfg")}
    (tmp_path / "ansible.cfg").write_text("")
    result = subprocess.run(
        [shutil.which("ansible-playbook"), str(playbook), "-i", "localhost,",
         "-e", f"ansible_python_interpreter={os.sys.executable}"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return yaml.safe_load(out.read_text())


def test_hermes_owned_keys_come_back_and_role_owned_keys_do_not(tmp_path):
    rendered = _run(tmp_path, LIVE)
    for key in ("_config_version", "_left_core_installed", "known_plugin_toolsets"):
        assert rendered[key] == LIVE[key], key
    assert isinstance(rendered["_left_core_installed"], list)
    assert isinstance(rendered["known_plugin_toolsets"], dict)
    assert isinstance(rendered["known_plugin_toolsets"]["cron"], list)
    assert rendered["monitoring"]["install_id"] == LIVE["monitoring"]["install_id"]
    assert "model" not in rendered and "mcp_servers" not in rendered


@pytest.mark.parametrize("live", [None, {}], ids=("missing", "empty"))
def test_a_home_without_a_config_carries_nothing(tmp_path, live):
    rendered = _run(tmp_path, live)
    assert set(rendered) == {"monitoring"}
    assert "install_id" not in rendered["monitoring"]
