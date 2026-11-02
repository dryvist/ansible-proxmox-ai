"""The read-only cron delivery probe hides every read and prints only routing fields."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/195-delivery-probe.yml").read_text())
TASKS = yaml.safe_load((ROLE / "tasks/delivery_probe.yml").read_text())
HOME = "/srv/hermes-home"
# Shapes of the credentials these files can hold (Slack, router, GitHub, generic sk-).
TOKEN_SHAPES = re.compile(r"xox[abpr]-|xapp-|sk-[A-Za-z0-9]|ghp_|github_pat_|Bearer ")


def _b64(text: str) -> dict:
    return {"content": base64.b64encode(text.encode()).decode()}


def _render(variables: dict):
    templar = Templar(loader=DataLoader())
    templar.available_variables = {"hermes_agent_hermes_home": HOME, **variables}
    rendered = templar.template(trust_as_template(DEFAULTS["hermes_agent_delivery_probe_report"]))
    return yaml.safe_load(rendered) if isinstance(rendered, str) else rendered


def _fixture() -> dict:
    config = {
        "model": {"api_key": "sk-router-secret-value"},
        "platforms": {"slack": {"token": "xoxb-1111-secret", "app_token": "xapp-1-secret"}},
        "gateway": {"profile_routes": [
            {"name": "homelab-admin-reports", "platform": "slack", "chat_id": "C0ROUTE1",
             "profile": "homelab-admin"},
            {"name": "splunk-admin-reports", "platform": "slack", "chat_id": "C0ROUTE2",
             "profile": "splunk-admin", "enabled": False},
        ]},
    }
    jobs = {"jobs": [{"id": "abc123", "name": "fabric-status", "deliver": "slack:C0ROUTE1",
                      "prompt": "use Bearer ghp_secretvalue", "env": {"TOKEN": "sk-job-secret"}}]}
    return {
        "hermes_agent_delivery_probe_primary_config": _b64(yaml.safe_dump(config)),
        "hermes_agent_delivery_probe_managed_config": {"failed": True},
        "hermes_agent_delivery_probe_jobs": {"results": [
            {"item": "homelab-admin", **_b64(json.dumps(jobs))},
            {"item": "splunk-admin", "failed": True},
        ]},
        "hermes_agent_delivery_probe_root": {"stdout": HOME},
        "hermes_agent_delivery_probe_homes": {"results": [
            {"item": "homelab-admin", "stdout": f"{HOME}/profiles/homelab-admin"},
            {"item": "splunk-admin", "stdout": f"{HOME}/profiles/splunk-admin"},
        ]},
    }


def test_every_read_is_hidden_and_only_the_report_prints() -> None:
    debug = [task for task in TASKS if "ansible.builtin.debug" in task]
    assert len(debug) == 1
    assert debug[0]["ansible.builtin.debug"]["msg"] == "{{ hermes_agent_delivery_probe_report }}"
    for task in TASKS:
        if task is debug[0]:
            continue
        assert task.get("no_log") is True, task["name"]
        assert set(task) & {"ansible.builtin.slurp", "ansible.builtin.command"}, task["name"]
        if "ansible.builtin.command" in task:
            assert task["changed_when"] is False
            assert task["ansible.builtin.command"]["argv"][0] == "realpath"


def test_report_projects_routes_jobs_and_homes() -> None:
    report = _render(_fixture())
    assert report["root_resolved"] == HOME
    assert report["routes"] == [
        {"source": "config", "profile": "homelab-admin", "chat_id": "C0ROUTE1", "enabled": True},
        {"source": "config", "profile": "splunk-admin", "chat_id": "C0ROUTE2", "enabled": False},
    ]
    admin, splunk = report["profiles"]
    assert admin["jobs"] == [{"id": "abc123", "name": "fabric-status", "deliver": "slack:C0ROUTE1"}]
    assert admin["expected_home"] == admin["resolved_home"] == f"{HOME}/profiles/homelab-admin"
    assert splunk["jobs_readable"] is False and splunk["jobs"] == []


def test_report_carries_no_token_shaped_string() -> None:
    assert not TOKEN_SHAPES.search(json.dumps(_render(_fixture())))


def test_probe_mode_ends_the_host_before_any_converge_task() -> None:
    main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    gate = next(i for i, task in enumerate(main) if task.get("name") == "Probe cron delivery routing and end the host")
    block = main[gate]["block"]
    assert main[gate]["when"] == "hermes_agent_delivery_probe_only | bool"
    assert block[-1] == {"name": "End the host after the delivery probe", "ansible.builtin.meta": "end_host"}
    assert not any("ansible.builtin.template" in task or "ansible.builtin.include_tasks" in task
                   for task in main[:gate] if task.get("name") != "Keep operational profiles off the private identity")
    assert DEFAULTS["hermes_agent_delivery_probe_only"] is False
