"""Probe runs skip isolated egress preparation; normal runs keep it."""

from pathlib import Path

import yaml

PLAY = Path(__file__).resolve().parents[2] / "playbooks" / "agent-egress.yml"
SKIP = "not (hermes_agent_delivery_probe_only | default(false) | bool)"


def _plays():
    return yaml.safe_load(PLAY.read_text())


def test_isolated_preparation_skips_in_probe_mode():
    prepare = _plays()[0]
    for task in prepare["pre_tasks"] + prepare["tasks"]:
        assert "inventory_hostname in hermes_agent_isolated_hosts" in task["when"]
        assert SKIP in task["when"]


def test_squid_converge_skips_in_probe_mode():
    squid = _plays()[1]
    block = next(t for t in squid["tasks"] if "block" in t)
    assert block["when"] == SKIP
