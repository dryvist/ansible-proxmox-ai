"""Contract for the campaign's interactive-latency gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
CAMPAIGN = PLAYBOOKS / "llm-model-campaign.yml"
TARGET = PLAYBOOKS / "llm-model-campaign-target.yml"
GATE = PLAYBOOKS / "tasks/llm-model-campaign-latency-gate.yml"
CELL = PLAYBOOKS / "tasks/llm-model-campaign-cell.yml"
DEFAULTS = PLAYBOOKS / "vars/llm-model-campaign-latency.yml"


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_latency_gate_defaults_query_and_fail_closed_contract():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert defaults["llm_campaign_latency_gate_enabled"] is True
    assert defaults["llm_campaign_interactive_key_aliases"] == [
        "hermes-default",
        "litellm-local-workstation",
    ]
    assert defaults["llm_campaign_latency_max_ratio"] == 1.2

    query = defaults["_llm_campaign_latency_query"]
    assert query.count("litellm_request_total_latency_metric_bucket") == 2
    assert query.count('api_key_alias=~"{{ _llm_campaign_latency_alias_regex }}"') == 2
    assert "[30m]" in query
    assert "[7d] offset 1d" in query
    assert "tofu_data']['containers" in defaults["_llm_campaign_latency_prometheus_host"]
    assert "prometheus_web" in defaults["_llm_campaign_latency_prometheus_port"]

    tasks = yaml.safe_load(GATE.read_text(encoding="utf-8"))
    request = _named(tasks, "Query the interactive p95 latency ratio from Prometheus")
    assert request["ansible.builtin.uri"]["method"] == "GET"
    assert "/api/v1/query?query=" in request["ansible.builtin.uri"]["url"]
    assert request["failed_when"] is False
    assert request["delegate_to"] == "localhost"

    guard = _named(tasks, "Fail closed on query errors or a regressed interactive p95")
    conditions = "\n".join(guard["ansible.builtin.assert"]["that"])
    assert "response.status" in conditions
    assert "response.json.status" in conditions
    assert "'NaN'" in conditions
    assert "llm_campaign_latency_max_ratio" in conditions
    assert " | length == 0" in conditions


def test_gate_runs_before_campaign_work_and_between_cells():
    campaign = yaml.safe_load(CAMPAIGN.read_text(encoding="utf-8"))[0]
    tasks = campaign["tasks"]
    gate = _named(tasks, "Gate the campaign on interactive request latency")
    workspace = _named(tasks, "Create a temporary workspace for the pinned benchmark config")
    assert gate["ansible.builtin.import_tasks"] == "tasks/llm-model-campaign-latency-gate.yml"
    assert tasks.index(gate) < tasks.index(workspace)
    assert campaign["vars_files"] == ["vars/llm-model-campaign-latency.yml"]

    target = yaml.safe_load(TARGET.read_text(encoding="utf-8"))[0]
    assert target["vars_files"] == ["vars/llm-model-campaign-latency.yml"]

    cells = yaml.safe_load(CELL.read_text(encoding="utf-8"))
    assert cells[0]["ansible.builtin.import_tasks"] == "llm-model-campaign-latency-gate.yml"
