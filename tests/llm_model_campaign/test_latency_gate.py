"""Contract for the campaign's interactive-latency gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
CAMPAIGN = PLAYBOOKS / "llm-model-campaign.yml"
TARGET = PLAYBOOKS / "llm-model-campaign-target.yml"
GATE = PLAYBOOKS / "tasks/llm-model-campaign-latency-gate.yml"
COUNTS = PLAYBOOKS / "tasks/llm-model-campaign-latency-gate-counts.yml"
RATIO = PLAYBOOKS / "tasks/llm-model-campaign-latency-gate-ratio.yml"
CLASSIFY = PLAYBOOKS / "tasks/llm-model-campaign-latency-gate-classify-response.yml"
REPORT = PLAYBOOKS / "tasks/llm-model-campaign-latency-gate-report.yml"
CELL = PLAYBOOKS / "tasks/llm-model-campaign-cell.yml"
DEFAULTS = PLAYBOOKS / "vars/llm-model-campaign-latency.yml"


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_latency_gate_defaults_query_and_fail_closed_contract():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert defaults["llm_campaign_latency_gate_enabled"] is True
    assert defaults["llm_campaign_interactive_key_aliases"] == ["hermes-default"]
    assert defaults["llm_campaign_interactive_scope_labels"] == {
        "tag_telemetry_tier_gpu": "true",
        "api_provider": "hosted_vllm",
    }
    assert defaults["llm_campaign_latency_max_ratio"] == 1.2
    assert defaults["llm_campaign_latency_min_samples"] == 20
    assert defaults["llm_campaign_latency_windows"] == ["30m", "2h", "6h"]

    selector = defaults["_llm_campaign_latency_bucket_selector"]
    assert 'api_key_alias=~"{{ _llm_campaign_latency_alias_regex }}"' in selector
    rendered_selector = Environment().from_string(selector).render(
        _llm_campaign_latency_alias_regex="hermes-default",
        llm_campaign_interactive_scope_labels=defaults["llm_campaign_interactive_scope_labels"],
    )
    assert rendered_selector == (
        'api_key_alias=~"hermes-default",api_provider="hosted_vllm",tag_telemetry_tier_gpu="true"'
    )
    count_query = defaults["_llm_campaign_latency_count_query"]
    assert "sum(increase(litellm_request_total_latency_metric_bucket" in count_query
    assert 'le="+Inf"' in count_query
    assert "[{{ item }}]" in count_query
    baseline_query = defaults["_llm_campaign_latency_baseline_query"]
    query = defaults["_llm_campaign_latency_query"]
    for query_template in (count_query, baseline_query, query):
        assert "{{ _llm_campaign_latency_bucket_selector }}" in query_template
    assert "[7d] offset 1d" in baseline_query
    assert "[7d] offset 1d" in query
    assert "_llm_campaign_latency_selected_window.window" in query
    assert "tofu_data']['containers" in defaults["_llm_campaign_latency_prometheus_host"]
    assert "prometheus_web" in defaults["_llm_campaign_latency_prometheus_port"]

    tasks = yaml.safe_load(GATE.read_text(encoding="utf-8"))
    count_tasks = yaml.safe_load(COUNTS.read_text(encoding="utf-8"))
    ratio_tasks = yaml.safe_load(RATIO.read_text(encoding="utf-8"))
    request_names = (
        "Query interactive request counts across latency windows",
        "Query the interactive baseline p95 from Prometheus",
        "Query the interactive p95 ratio from Prometheus",
    )
    for request, request_name in (
        (_named(count_tasks, request_names[0]), request_names[0]),
        (_named(ratio_tasks, request_names[1]), request_names[1]),
        (_named(ratio_tasks, request_names[2]), request_names[2]),
    ):
        assert request["ansible.builtin.uri"]["method"] == "GET"
        assert "/api/v1/query?query=" in request["ansible.builtin.uri"]["url"]
        assert request["failed_when"] is False
        assert request["changed_when"] is False
        assert request["delegate_to"] == "localhost"

    assert _named(count_tasks, request_names[0])["loop"] == "{{ llm_campaign_latency_windows }}"
    assert _named(tasks, "Count interactive requests and choose a latency window")[
        "ansible.builtin.include_tasks"
    ] == "llm-model-campaign-latency-gate-counts.yml"
    assert _named(tasks, "Compare interactive p95 latency with its baseline")[
        "ansible.builtin.include_tasks"
    ] == "llm-model-campaign-latency-gate-ratio.yml"

    report_tasks = yaml.safe_load(REPORT.read_text(encoding="utf-8"))
    guard = _named(report_tasks, "Fail closed on interactive latency gate failures")
    conditions = "\n".join(guard["ansible.builtin.assert"]["that"])
    message = guard["ansible.builtin.assert"]["fail_msg"]
    assert conditions == "_llm_campaign_latency_reason in ['pass', 'idle: nothing to protect']"
    for value in (
        "HTTP status=",
        "Prometheus status=",
        "result count=",
        "raw ratio=",
        "baseline p95=",
        "max ratio=",
        "window=",
        "sample count=",
        "largest window=",
        "count=",
    ):
        assert value in message

    report = _named(report_tasks, "Report the interactive latency gate result")
    assert "ratio=" in report["ansible.builtin.debug"]["msg"]
    assert "max=" in report["ansible.builtin.debug"]["msg"]
    assert "window=" in report["ansible.builtin.debug"]["msg"]
    assert "samples=" in report["ansible.builtin.debug"]["msg"]
    assert report["when"] == "_llm_campaign_latency_reason == 'pass'"
    assert all("llm_campaign_latency_gate_enabled | bool" in str(task.get("when", "")) for task in tasks)


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


def test_scalar_samples_require_typed_non_negative_values():
    tasks = yaml.safe_load(CLASSIFY.read_text(encoding="utf-8"))
    capture = _named(tasks, "Capture the interactive Prometheus scalar response")
    well_formed = capture["ansible.builtin.set_fact"][
        "_llm_campaign_latency_classification_observation"
    ]["well_formed"]
    assert "['value'][0] is number" in well_formed
    assert "['value'][1] is string" in well_formed
    assert "match('^[+]?(" in well_formed
    assert "[+-]?([0-9]" not in well_formed
    assert ".get('resultType') == 'vector'" in well_formed


def test_failure_report_lists_per_window_counts():
    tasks = yaml.safe_load(REPORT.read_text(encoding="utf-8"))
    report = _named(tasks, "Fail closed on interactive latency gate failures")
    assert "window counts=" in report["ansible.builtin.assert"]["fail_msg"]
    assert "default('none', true)" not in report["ansible.builtin.assert"]["fail_msg"].split(
        "result count="
    )[1].split(";")[0]


def test_gate_state_resets_to_real_nulls():
    tasks = yaml.safe_load(GATE.read_text(encoding="utf-8"))
    reset = _named(tasks, "Reset interactive latency gate state")["ansible.builtin.set_fact"]
    for key in (
        "_llm_campaign_latency_selected_window",
        "_llm_campaign_latency_largest_window",
        "_llm_campaign_latency_reason",
        "_llm_campaign_latency_classification_reason",
        "_llm_campaign_latency_classification_kind",
    ):
        assert reset[key] is None, key


def test_empty_successful_count_result_counts_as_zero_requests():
    tasks = yaml.safe_load(COUNTS.read_text(encoding="utf-8"))
    capture = _named(tasks, "Capture interactive request counts for each latency window")
    template = capture["vars"]["_llm_campaign_latency_count_sample"]
    env = Environment()

    def render(status: str, result: Any) -> str:
        return env.from_string(template).render(
            _llm_campaign_latency_count_status=status,
            _llm_campaign_latency_count_result=result,
        ).strip()

    well_formed = capture["vars"]["_llm_campaign_latency_count_well_formed"]
    assert "['value'][0] is number" in well_formed
    assert "['value'][1] is string" in well_formed
    assert "match('^[+]?(" in well_formed  # no negative sample counts
    assert ".get('resultType') == 'vector'" in well_formed

    assert render("success", []) == "0"
    assert render("success", [{"value": [1, "42"]}]) == "42"
    assert render("error", []) == "None"
