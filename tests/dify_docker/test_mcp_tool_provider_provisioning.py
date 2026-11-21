"""Dify MCP providers are reconciled from OpenBao before catalog DSL import."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "roles/dify_docker/tasks/main.yml"
TASKS = ROOT / "roles/dify_docker/tasks/mcp_tool_provider_one.yml"
GROUP_VARS = ROOT / "inventory/group_vars/dify_group.yml"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks, name: str):
    return next(task for task in tasks if task.get("name") == name)


def test_vikunja_mcp_provider_is_declared_and_resolves_the_dsl_placeholder():
    group_vars = _yaml(GROUP_VARS)
    provider, = group_vars["dify_public_agent_mcp_providers"]

    assert provider["name"] == "vikunja"
    assert provider["server_identifier"] == "vikunja"
    assert provider["openbao_field"] == "VIKUNJA_MCP_HTTP_TOKEN_VIKUNJA_CLERK"
    assert provider["tool_name"] == "vikunja_tasks"
    assert group_vars["dify_public_agent_tool_providers"]["vikunja-clerk"] == {
        "VIKUNJA_MCP_PROVIDER_ID": "vikunja"
    }


def test_mcp_provider_reconciliation_precedes_catalog_import():
    tasks = _yaml(MAIN)
    include = _task(tasks, "Ensure declared MCP tool providers")
    import_dsls = _task(tasks, "Import catalog agent DSLs for operator tuning")

    assert include["ansible.builtin.include_tasks"] == "mcp_tool_provider_one.yml"
    assert include["loop"] == "{{ dify_docker_agent_mcp_providers }}"
    assert include["loop_control"]["loop_var"] == "dify_docker_agent_mcp_provider"
    assert include["when"][0] == "dify_docker_provision_model_provider | bool"
    assert "| trim | length >= 32" in include["when"][1]
    assert tasks.index(include) < tasks.index(import_dsls)


def test_mcp_provider_api_uses_openbao_headers_and_check_mode_safe_writes():
    tasks = _yaml(TASKS)
    get_providers = _task(tasks, "Read current Dify workspace MCP providers")
    create = _task(tasks, "Register the declared Dify MCP provider")
    update = _task(tasks, "Update the declared Dify MCP provider")
    refresh = _task(tasks, "Refresh tools for the changed Dify MCP provider")
    token = _task(tasks, "Read the declared MCP provider bearer token from the OpenBao view")

    assert get_providers["ansible.builtin.uri"]["url"].endswith(
        "/workspaces/current/tool-providers?type=mcp"
    )
    assert create["ansible.builtin.uri"]["method"] == "POST"
    assert create["when"][0] == "not ansible_check_mode"
    assert create["no_log"] is True
    assert update["ansible.builtin.uri"]["method"] == "PUT"
    assert update["when"][0] == "not ansible_check_mode"
    assert update["no_log"] is True
    assert refresh["ansible.builtin.uri"]["method"] == "GET"
    assert refresh["when"][0] == "not ansible_check_mode"
    assert refresh["no_log"] is True
    assert token["no_log"] is True
    assert "bao_local_llm_secrets" in str(token["ansible.builtin.set_fact"])
    assert "| trim" in token["ansible.builtin.set_fact"]["dify_docker_mcp_provider_headers"]

    for task in (get_providers, _task(tasks, "Read the named Dify MCP provider details"), create, update, refresh):
        assert task["ansible.builtin.uri"]["follow_redirects"] == "none"
        assert task["ansible.builtin.uri"]["use_proxy"] is False


def test_a_provider_without_its_tool_is_authorized_then_refreshed():
    tasks = _yaml(TASKS)
    missing = _task(tasks, "Decide whether the named Dify MCP provider is missing its required tool")
    auth = _task(tasks, "Authorize the changed Dify MCP provider")
    refresh = _task(tasks, "Refresh tools for the changed Dify MCP provider")
    resolved = _task(tasks, "Keep the Dify MCP provider id for tool refresh")

    fact = missing["ansible.builtin.set_fact"]["dify_docker_mcp_provider_missing_tool"]
    assert "dify_docker_mcp_provider_details.json.tools" in fact
    assert "dify_docker_agent_mcp_provider.tool_name" in fact
    assert auth["ansible.builtin.uri"]["url"].endswith("/workspaces/current/tool-provider/mcp/auth")
    assert auth["ansible.builtin.uri"]["method"] == "POST"
    assert auth["ansible.builtin.uri"]["body"] == {"provider_id": "{{ dify_docker_mcp_provider_resolved_id }}"}
    assert auth["ansible.builtin.uri"]["use_proxy"] is False
    assert auth["when"][0] == "not ansible_check_mode"
    assert auth["no_log"] is True
    # The same three triggers drive the id, the authorize and the refresh.
    for task in (resolved, auth, refresh):
        assert "dify_docker_mcp_provider_missing_tool | bool" in task["when"][1]
        assert "dify_docker_mcp_provider_needs_reconcile | bool" in task["when"][1]
    assert tasks.index(missing) < tasks.index(resolved) < tasks.index(auth) < tasks.index(refresh)


def test_changed_configuration_is_applied_before_the_health_checks():
    tasks = _yaml(ROOT / "roles/dify_docker/tasks/config_and_deploy.yml")
    names = [task.get("name") for task in tasks]
    flush = _task(tasks, "Recreate Dify against changed configuration before the health checks")

    assert flush["ansible.builtin.meta"] == "flush_handlers"
    assert names.index("Render Dify squid (ssrf_proxy) configuration") < names.index(flush["name"])
    assert names.index("Deploy Dify via docker compose") < names.index(flush["name"])
    assert names.index(flush["name"]) < names.index("Wait for Dify web port to accept TCP connections")
