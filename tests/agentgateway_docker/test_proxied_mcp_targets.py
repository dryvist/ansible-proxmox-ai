"""Proxied remote MCP servers: gateway-held backend credential, fixed caller, tool allow-list."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from test_authz_render import ENV, FACADE_DEFAULTS, ROLE_DEFAULTS, _render, _route_for_path, _routes

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/agentgateway_docker/tasks/proxied_mcp.yml"
MAIN_TASKS = ROOT / "roles/agentgateway_docker/tasks/main.yml"
GROUP_VARS = ROOT / "inventory/group_vars/agentgateway_group.yml"
INBOUND = "i" * 40
TARGET = {
    "name": "example-mcp",
    "host": "https://mcp.example.com/mcp",
    "backend_token": "example-backend-credential",
    "inbound_token": INBOUND,
    "allowed_tools": ["search", "get_item"],
}
MCP_TARGETS = [{"name": "splunk", "host": "https://splunk.example.com/services/mcp", "tls_insecure": True}]


def _proxied(targets: list[dict[str, Any]]) -> str:
    return _render(
        extra={
            "agentgateway_docker_proxied_mcp_targets": targets,
            "agentgateway_docker_proxied_mcp_secrets_path": ROLE_DEFAULTS["agentgateway_docker_proxied_mcp_secrets_path"],
        }
    )


def test_role_default_is_an_empty_list() -> None:
    assert ROLE_DEFAULTS["agentgateway_docker_proxied_mcp_targets"] == []


def test_no_proxied_targets_render_the_existing_config() -> None:
    assert _proxied([]) == _render()


def test_proxied_target_renders_fixed_caller_tool_allow_list_and_file_backend_auth() -> None:
    route = _route_for_path(_routes(_proxied([TARGET])), "/example-mcp")
    policies = route["policies"]

    api_key = policies["apiKey"]
    assert api_key["mode"] == "strict"
    assert api_key["location"] == {"header": {"name": "authorization", "prefix": "Bearer "}}
    assert api_key["keys"] == [
        {"keyHash": "sha256:" + hashlib.sha256(INBOUND.encode()).hexdigest(), "metadata": {"caller": "example-mcp"}}
    ]
    assert INBOUND not in json.dumps(route)

    assert policies["requestHeaderModifier"]["remove"] == FACADE_DEFAULTS["agentgateway_docker_caller_credential_headers"]
    assert policies["mcpAuthorization"]["rules"] == [
        {"allow": 'mcp.tool.name == "search"'},
        {"allow": 'mcp.tool.name == "get_item"'},
    ]

    target = route["backends"][0]["mcp"]["targets"][0]
    assert target["mcp"]["host"] == TARGET["host"]
    assert target["policies"]["backendAuth"]["key"] == {
        "value": {"file": "/proxied-mcp-secrets/example-mcp"},
        "location": {"header": {"name": "authorization", "prefix": "Bearer "}},
    }
    assert TARGET["backend_token"] not in json.dumps(route)


def test_backend_prefix_override_is_rendered() -> None:
    route = _route_for_path(_routes(_proxied([{**TARGET, "backend_token_prefix": "Token "}])), "/example-mcp")
    header = route["backends"][0]["mcp"]["targets"][0]["policies"]["backendAuth"]["key"]["location"]["header"]
    assert header["prefix"] == "Token "


def _assert_conditions(item: dict[str, Any]) -> bool:
    task = yaml.safe_load(TASKS.read_text())[0]
    reserved = ENV.from_string(task["vars"]["agentgateway_docker_proxied_reserved_names"]).render(
        agentgateway_docker_mcp_targets=MCP_TARGETS,
        agentgateway_docker_facade_path=FACADE_DEFAULTS["agentgateway_docker_facade_path"],
    )
    context = {"item": item, "agentgateway_docker_proxied_reserved_names": yaml.safe_load(reserved)}
    return all(ENV.compile_expression(cond)(**context) for cond in task["ansible.builtin.assert"]["that"])


def test_valid_item_passes_the_assertions() -> None:
    assert _assert_conditions(TARGET)


@pytest.mark.parametrize(
    "change",
    [
        {"inbound_token": "short"},
        {"allowed_tools": []},
        {"allowed_tools": ['x" || true || "']},
        {"name": "Bad Name"},
        {"name": "splunk"},
        {"name": "agents"},
        {"name": "vikunja-clerk"},
        {"backend_token": ""},
        {"host": "ftp://mcp.example.com"},
    ],
)
def test_invalid_item_fails_the_assertions(change: dict[str, Any]) -> None:
    assert not _assert_conditions({**TARGET, **change})


def test_duplicate_names_fail() -> None:
    task = yaml.safe_load(TASKS.read_text())[1]
    names = ENV.from_string(task["vars"]["agentgateway_docker_proxied_names"]).render(
        agentgateway_docker_proxied_mcp_targets=[TARGET, TARGET]
    )
    condition = task["ansible.builtin.assert"]["that"][0]
    assert not ENV.compile_expression(condition)(agentgateway_docker_proxied_names=yaml.safe_load(names))


def test_proxied_target_needs_no_owner_entry() -> None:
    owner_task = "Validate that every MCP target has an owner or a recorded exception"
    task = next(t for t in yaml.safe_load(MAIN_TASKS.read_text()) if t.get("name") == owner_task)
    expression = task["vars"]["_ownerless_targets"]
    ownerless = ENV.from_string(expression).render(
        agentgateway_docker_mcp_targets=MCP_TARGETS,
        agentgateway_docker_proxied_mcp_targets=[TARGET],
        ai_agent_owner={"splunk": "splunk-heimdall"},
        agentgateway_docker_owner_exceptions=[],
    )
    assert yaml.safe_load(ownerless) == []


def _group_var(env: dict[str, str], secrets: dict[str, str]) -> list[dict[str, Any]]:
    expression = yaml.safe_load(GROUP_VARS.read_text())["agentgateway_docker_proxied_mcp_targets"]
    rendered = ENV.from_string(expression).render(
        lookup=lambda kind, key: env.get(key, ""),
        bao_local_llm_secrets=secrets,
    )
    return yaml.safe_load(rendered)


def test_group_var_is_empty_without_the_env_key() -> None:
    assert _group_var({}, {}) == []


def test_group_var_resolves_fields_and_skips_unseeded_items() -> None:
    items = [
        {
            "name": "example-mcp",
            "host": "https://mcp.example.com/mcp",
            "allowed_tools": ["search"],
            "backend_token_field": "EXAMPLE_BACKEND",
            "inbound_token_field": "EXAMPLE_INBOUND",
        },
        {
            "name": "unseeded-mcp",
            "host": "https://other.example.com/mcp",
            "allowed_tools": ["search"],
            "backend_token_field": "MISSING_BACKEND",
            "inbound_token_field": "MISSING_INBOUND",
        },
    ]
    env = {"AGENTGATEWAY_PROXIED_MCP_TARGETS": json.dumps(items), "EXAMPLE_INBOUND": INBOUND}
    targets = _group_var(env, {"EXAMPLE_BACKEND": "bao-backend"})
    assert targets == [
        {
            "name": "example-mcp",
            "host": "https://mcp.example.com/mcp",
            "allowed_tools": ["search"],
            "backend_token": "bao-backend",
            "inbound_token": INBOUND,
        }
    ]
