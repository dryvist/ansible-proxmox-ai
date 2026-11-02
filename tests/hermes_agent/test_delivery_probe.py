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
        "hermes_agent_delivery_probe_active_profile": _b64("homelab-admin\n"),
        "hermes_agent_delivery_probe_routes": {"results": [
            {"item": "homelab-admin", "stdout": json.dumps({
                "routes": 1, "raw_routes": 2, "matching_profile": 1,
                "primary_is_current": False, "private_field": "must-not-report",
            })},
            {"item": "splunk-admin", "stdout": json.dumps({
                "routes": 0, "raw_routes": 2, "matching_profile": 1,
                "primary_is_current": False, "error": "ValueError",
                "message": "invalid route configuration", "frames": ["config_loader.py:42"],
            })},
        ]},
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
            argv = task["ansible.builtin.command"]["argv"]
            if "Inspect primary routes" in task["name"]:
                assert task["check_mode"] is False
                assert task["become_user"] == "{{ hermes_agent_user }}"
                assert task["environment"]["HERMES_HOME"] == (
                    "{{ hermes_agent_hermes_home }}/profiles/{{ item }}"
                )
                assert argv[:2] == ["{{ hermes_agent_venv_python }}", "-c"]
                python = argv[2]
                assert "_primary_profile_routes_for_current_home()" in python
                assert "read_yaml_layers" in python and "parse_profile_routes" in python
                assert "profile_matches_home" in python
                assert python.count("print(") == 1
                assert "print(json.dumps(result))" in python
                assert "print(routes" not in python and "print(route" not in python
            else:
                assert argv[0] == "realpath"


def test_report_projects_routes_jobs_and_homes() -> None:
    report = _render(_fixture())
    assert report["root_resolved"] == HOME
    assert report["root_active_profile"] == "homelab-admin"
    assert report["routes"] == [
        {"source": "config", "profile": "homelab-admin", "chat_id": "C0ROUTE1", "enabled": True},
        {"source": "config", "profile": "splunk-admin", "chat_id": "C0ROUTE2", "enabled": False},
    ]
    admin, splunk = report["profiles"]
    assert admin["route_lookup"] == {
        "routes": 1, "raw_routes": 2, "matching_profile": 1, "primary_is_current": False,
    }
    assert splunk["route_lookup"] == {
        "routes": 0, "raw_routes": 2, "matching_profile": 1, "primary_is_current": False,
        "error": "ValueError", "message": "invalid route configuration",
        "frames": ["config_loader.py:42"],
    }
    assert admin["jobs"] == [{"id": "abc123", "name": "fabric-status", "deliver": "slack:C0ROUTE1"}]
    assert admin["expected_home"] == admin["resolved_home"] == f"{HOME}/profiles/homelab-admin"
    assert splunk["jobs_readable"] is False and splunk["jobs"] == []


def test_report_carries_no_token_shaped_string() -> None:
    assert not TOKEN_SHAPES.search(json.dumps(_render(_fixture())))


def test_missing_active_profile_reports_empty_name() -> None:
    fixture = _fixture()
    fixture["hermes_agent_delivery_probe_active_profile"] = {"failed": True}
    assert _render(fixture)["root_active_profile"] == ""


def test_probe_mode_ends_the_host_before_any_converge_task() -> None:
    main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    gate = next(i for i, task in enumerate(main) if task.get("name") == "Probe cron delivery routing and end the host")
    block = main[gate]["block"]
    assert main[gate]["when"] == "hermes_agent_delivery_probe_only | bool"
    assert block[-1] == {"name": "End the host after the delivery probe", "ansible.builtin.meta": "end_host"}
    assert not any("ansible.builtin.template" in task or "ansible.builtin.include_tasks" in task
                   for task in main[:gate] if task.get("name") != "Keep operational profiles off the private identity")
    assert DEFAULTS["hermes_agent_delivery_probe_only"] is False
