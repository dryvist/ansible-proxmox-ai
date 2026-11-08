"""OpenBao-published Dify MCP URLs populate guarded agentgateway routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from test_authz_render import ENV, ROLE_DEFAULTS, _render, _route_for_path, _routes

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "playbooks/ai-orchestration.yml"
TASK_FILE = ROOT / "playbooks/tasks/agentgateway_dify_mcp_targets.yml"
ROLE_TASKS = ROOT / "roles/agentgateway_docker/tasks/main.yml"
APP_NAMES = ["ci-fixer", "code-simplifier"]
CALLERS = {
    "hermes": {"trust_tier": "trusted"},
    "trusted-harness": {"trust_tier": "trusted"},
    "untrusted-harness": {"trust_tier": "untrusted"},
}
ROSTER_KEYS = {
    "hermes": "fixture-hermes-key",
    "trusted-harness": "fixture-harness-key",
}


def _task(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(task for task in tasks if task.get("name") == name)


def _agentgateway_play() -> dict[str, Any]:
    plays = yaml.safe_load(PLAYBOOK.read_text())
    return next(play for play in plays if play.get("name") == "Configure agentgateway MCP/LLM/A2A data plane")


def _dify_block() -> dict[str, Any]:
    tasks = yaml.safe_load(TASK_FILE.read_text())
    assert len(tasks) == 1
    return tasks[0]


def _dify_tasks() -> list[dict[str, Any]]:
    return _dify_block()["block"]


def _dify_task(name: str) -> dict[str, Any]:
    return _task(_dify_tasks(), name)


def _render_expression(expression: str, **context: Any) -> Any:
    rendered = ENV.from_string(expression.strip()).render(**context)
    return yaml.safe_load(rendered)


def _targets_from_reads(reads: list[dict[str, Any]]) -> list[dict[str, str]]:
    expression = _dify_task("Build Dify MCP targets from the OpenBao leaves")["ansible.builtin.set_fact"][
        "agentgateway_docker_dify_mcp_targets"
    ]
    return _render_expression(expression, _agentgateway_dify_mcp_leaf_reads={"results": reads})


def _invalid_apps(targets: list[dict[str, str]]) -> list[str]:
    expression = _dify_task("Collect Dify MCP apps with missing or invalid URLs")["ansible.builtin.set_fact"][
        "_agentgateway_dify_mcp_invalid_apps"
    ]
    hostvars = {"localhost": {"agentgateway_docker_dify_mcp_targets": targets}}
    return _render_expression(expression, hostvars=hostvars)


def _roster(targets: list[dict[str, str]]) -> list[dict[str, Any]]:
    expression = ROLE_DEFAULTS["agentgateway_docker_agent_roster"]
    return _render_expression(
        expression,
        ai_callers=CALLERS,
        agentgateway_docker_a2a_allowed_tier="trusted",
        agentgateway_docker_roster_keys=ROSTER_KEYS,
        agentgateway_docker_dify_mcp_targets=targets,
    )


def test_two_openbao_leaves_build_targets_and_share_hermes_callers() -> None:
    reads = [
        {
            "item": "ci-fixer",
            "status": 200,
            "json": {"data": {"data": {"url": "https://dify.example.test/mcp/server/code-one/mcp"}}},
        },
        {
            "item": "code-simplifier",
            "status": 200,
            "json": {"data": {"data": {"url": "https://dify.example.test/mcp/server/code-two/mcp"}}},
        },
    ]
    targets = _targets_from_reads(reads)
    roster = _roster(targets)

    assert targets == [
        {"name": "ci-fixer", "url": "https://dify.example.test/mcp/server/code-one/mcp"},
        {"name": "code-simplifier", "url": "https://dify.example.test/mcp/server/code-two/mcp"},
    ]
    hermes_callers = next(entry["allowed_callers"] for entry in roster if entry["agent"] == "hermes")
    assert hermes_callers == ["hermes", "trusted-harness"]
    for app in APP_NAMES:
        assert next(entry["allowed_callers"] for entry in roster if entry["agent"] == app) == hermes_callers


def test_missing_prefix_builds_no_targets_and_leaves_the_default_roster_unchanged() -> None:
    list_task = _dify_task("Classify the Dify MCP metadata list response")["ansible.builtin.set_fact"]
    missing_prefix = {"status": 404}
    status = _render_expression(
        list_task["_agentgateway_dify_mcp_list_status"],
        _agentgateway_dify_mcp_list_result=missing_prefix,
    )
    app_names = _render_expression(
        list_task["_agentgateway_dify_mcp_app_names"],
        _agentgateway_dify_mcp_list_result=missing_prefix,
    )
    targets = _targets_from_reads([])

    assert status == 404
    assert app_names == []
    assert targets == []
    assert _roster(targets) == [
        {"agent": "hermes", "allowed_callers": ["hermes", "trusted-harness"]},
        {"agent": "langgraph", "allowed_callers": ["hermes", "trusted-harness"]},
    ]


def test_list_response_keys_become_app_names() -> None:
    """The KV v2 list field is named `keys`, which dot notation would resolve to the dict method instead."""
    expression = _dify_task("Classify the Dify MCP metadata list response")["ansible.builtin.set_fact"][
        "_agentgateway_dify_mcp_app_names"
    ]
    listed = {"status": 200, "json": {"data": {"keys": APP_NAMES}}}
    assert _render_expression(expression, _agentgateway_dify_mcp_list_result=listed) == APP_NAMES
    missing = {"status": 404, "json": {"errors": []}}
    assert _render_expression(expression, _agentgateway_dify_mcp_list_result=missing) == []


def test_openbao_reads_use_kv_v2_endpoints_with_the_existing_token() -> None:
    list_request = _dify_task("List Dify MCP app leaves from OpenBao")["ansible.builtin.uri"]
    leaf_request = _dify_task("Read each Dify MCP app leaf from OpenBao")["ansible.builtin.uri"]

    assert "/v1/{{ _agentgateway_dify_mcp_metadata_path }}/?list=true" in list_request["url"]
    assert list_request["status_code"] == [200, 404]
    assert list_request["return_content"] is True
    assert list_request["headers"]["X-Vault-Token"] == "{{ openbao_secrets_converge_writer_token }}"
    assert "/v1/{{ _agentgateway_dify_mcp_data_path }}/{{ item }}" in leaf_request["url"]
    assert leaf_request["status_code"] == [200, 404]
    assert leaf_request["return_content"] is True
    assert leaf_request["headers"]["X-Vault-Token"] == "{{ openbao_secrets_converge_writer_token }}"


def test_list_failures_report_path_and_status_without_exposing_service_values() -> None:
    list_task = _dify_task("Classify the Dify MCP metadata list response")["ansible.builtin.set_fact"]
    fail_task = _dify_task("Fail when Dify MCP metadata cannot be listed")
    message_expression = fail_task["ansible.builtin.fail"]["msg"]

    for response_status in (403, 503):
        status = _render_expression(
            list_task["_agentgateway_dify_mcp_list_status"],
            _agentgateway_dify_mcp_list_result={"status": response_status},
        )
        message = ENV.from_string(message_expression).render(
            _agentgateway_dify_mcp_metadata_path="secret/metadata/ai/dify/agent-mcp",
            hostvars={"localhost": {"_agentgateway_dify_mcp_list_status": status}},
        )
        assert status == response_status
        assert "secret/metadata/ai/dify/agent-mcp" in message
        assert f"HTTP {response_status}" in message
        assert "https://" not in message
        assert "token" not in message.lower()


def test_leaf_read_failures_report_data_path_and_status_without_url() -> None:
    error_expression = _dify_task("Collect Dify MCP leaf read errors without URLs")["ansible.builtin.set_fact"][
        "_agentgateway_dify_mcp_read_errors"
    ]
    errors = _render_expression(
        error_expression,
        _agentgateway_dify_mcp_leaf_reads={"results": [{"item": "ci-fixer", "status": 404}]},
    )
    fail_task = _dify_task("Fail when a Dify MCP app leaf cannot be read")
    message = ENV.from_string(fail_task["ansible.builtin.fail"]["msg"]).render(
        _agentgateway_dify_mcp_data_path="secret/data/ai/dify/agent-mcp",
        item=errors[0],
    )

    assert "secret/data/ai/dify/agent-mcp/ci-fixer" in message
    assert "HTTP 404" in message
    assert "https://" not in message


def test_missing_or_non_http_leaf_urls_fail_with_the_app_name_only() -> None:
    error_expression = _dify_task("Collect Dify MCP leaf read errors without URLs")["ansible.builtin.set_fact"][
        "_agentgateway_dify_mcp_read_errors"
    ]
    errors = _render_expression(
        error_expression,
        _agentgateway_dify_mcp_leaf_reads={
            "results": [
                {"item": "ci-fixer", "status": 200, "json": {"data": {"data": {}}}},
                {"item": "code-simplifier", "status": 403},
            ]
        },
    )
    assert errors == [{"app": "code-simplifier", "status": 403}]

    fail_message = _dify_task("Fail when a Dify MCP app URL is missing or invalid")["ansible.builtin.fail"]["msg"]
    for url in ("", "file:///mcp/server/code"):
        targets = [{"name": "ci-fixer", "url": url}]
        assert _invalid_apps(targets) == ["ci-fixer"]
        message = ENV.from_string(fail_message).render(
            hostvars={"localhost": {"_agentgateway_dify_mcp_invalid_apps": ["ci-fixer"]}}
        )
        assert "ci-fixer" in message
        if url:
            assert url not in message


def test_fixture_urls_render_as_the_two_path_scoped_agent_routes() -> None:
    targets = [
        {"name": "ci-fixer", "url": "https://dify.example.test/mcp/server/<code>/mcp"},
        {"name": "code-simplifier", "url": "https://dify.example.test/mcp/server/<code>/mcp"},
    ]
    roster = _roster(targets)
    routes = _routes(
        _render(
            extra={
                "agentgateway_docker_dify_mcp_targets": targets,
                "agentgateway_docker_agent_roster": roster,
            }
        )
    )

    assert _route_for_path(routes, "/agents/ci-fixer")["backends"][0]["mcp"]["targets"][0]["mcp"]["host"] == targets[0]["url"]
    assert _route_for_path(routes, "/agents/code-simplifier")["backends"][0]["mcp"]["targets"][0]["mcp"]["host"] == targets[1]["url"]


def test_every_task_that_reads_holds_or_renders_a_dify_url_suppresses_logs() -> None:
    play = _agentgateway_play()
    play_tasks = play["pre_tasks"]
    role_tasks = yaml.safe_load(ROLE_TASKS.read_text())
    imported_task = _task(play_tasks, "Load Dify MCP targets from OpenBao")
    assert imported_task["ansible.builtin.import_tasks"] == "tasks/agentgateway_dify_mcp_targets.yml"
    include_task = _task(_task(play["tasks"], "Converge agentgateway_docker (isolated — failure does not abort downstream plays)")["block"], "Include agentgateway_docker role")
    assert include_task["no_log"] is True

    block = _dify_block()
    assert block["delegate_to"] == "localhost"
    assert block["connection"] == "local"
    assert block["become"] is False
    assert block["vars"]["ansible_become"] is False
    assert block["run_once"] is True
    assert block["no_log"] is True
    assert [task["name"] for task in block["block"] if task.get("no_log") is False] == [
        "Fail when Dify MCP metadata cannot be listed",
        "Validate Dify MCP app names from OpenBao",
        "Fail when a Dify MCP app leaf cannot be read",
        "Fail when a Dify MCP app URL is missing or invalid",
    ]

    markers = (
        "ansible.builtin.uri",
        "_agentgateway_dify_mcp_leaf_reads",
        "secret.get('url'",
        "target.url",
        "agentgateway_docker_dify_mcp_targets",
    )
    assert any(marker in json.dumps(include_task) for marker in markers)
    for task in block["block"]:
        serialized = json.dumps(task)
        if any(marker in serialized for marker in markers):
            assert task.get("no_log", block["no_log"]) is True, task.get("name")

    for task in role_tasks:
        serialized = json.dumps(task)
        if "target.url" in serialized or task.get("name") == "Deploy agentgateway config":
            assert task.get("no_log") is True, task.get("name")
    assert _task(role_tasks, "Validate Dify MCP target URLs")["no_log"] is True
