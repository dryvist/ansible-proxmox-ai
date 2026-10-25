"""Per-agent keys protect owned agentgateway routes without changing defaults."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml
from ansible.plugins.filter.core import FilterModule
from cel_subset import authorize

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = REPO_ROOT / "inventory/group_vars/all.yml"
DEFAULTS = REPO_ROOT / "roles/agentgateway_docker/defaults/main/00-gateway.yml"
MCP_ROUTE_AUTH_DEFAULTS = REPO_ROOT / "roles/agentgateway_docker/defaults/main/30-mcp-route-auth.yml"
TASKS = REPO_ROOT / "roles/agentgateway_docker/tasks/main.yml"
TEMPLATE = REPO_ROOT / "roles/agentgateway_docker/templates/config.yaml.j2"
BASELINE_SHA256 = "c6f329d4ff68fd4fbb1466bcda56033352413ab9e973ddcdf650825964f255b6"
MCP_ROUTE_PATHS = ("/memory", "/docs", "/vikunja", "/nautobot")

AGENTS = {
    "splunk-heimdall": {"owns": ["splunk"]},
    "other-agent": {"owns": []},
}
KEYS = {
    "splunk_heimdall_agentgateway_key": "example-key-splunk-owner",
    "other_agent_agentgateway_key": "example-key-other-agent",
}

ENV = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True)
ENV.filters.update(FilterModule().filters())
INVENTORY_VARS = yaml.safe_load(INVENTORY.read_text())
ROLE_DEFAULTS = {
    **yaml.safe_load(DEFAULTS.read_text()),
    **yaml.safe_load(MCP_ROUTE_AUTH_DEFAULTS.read_text()),
    **yaml.safe_load((DEFAULTS.parent / "05-a2a-targets.yml").read_text()),
}
HINDSIGHT_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles/agentgateway_docker/defaults/main/20-hindsight-acl.yml").read_text()
)
TEMPLATE_SOURCE = TEMPLATE.read_text()


def _render_map(expression: str, **context: Any) -> dict[str, Any]:
    return yaml.safe_load(ENV.from_string(expression).render(**context))


def _render_callers(roster: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """`ai_callers` as inventory/group_vars/all.yml derives it: registry plus roster."""
    # The test environment's strict undefined cannot evaluate `| default`, so an unloaded
    # registry is passed as empty, which is what the default yields in a play.
    return _render_map(INVENTORY_VARS["ai_callers"], ai_agents=roster, agent_callers_registry=registry or {})


def _render_key_fields(roster: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    return _render_map(
        INVENTORY_VARS["ai_agent_gateway_key_fields"], ai_callers=_render_callers(roster, registry)
    )


def _render(
    agents: dict[str, Any] | None = None,
    owners: dict[str, str] | None = None,
    keys: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    template_source: str | None = None,
    registry: dict[str, Any] | None = None,
) -> str:
    roster = agents or {}
    key_fields = _render_key_fields(roster, registry)
    roster_keys = _render_map(
        ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
        ai_agent_gateway_key_fields=key_fields,
        openbao_secrets_by_domain={"agentgateway-agent-keys": keys or {}},
    )
    callers = _render_callers(roster, registry)
    roster_tiers = _render_map(
        HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_roster_tiers"],
        agentgateway_docker_roster_keys=roster_keys,
        ai_callers=callers,
    )
    context = {
        "ansible_managed": "managed by test",
        "agentgateway_docker_admin_port": 15001,
        "agentgateway_docker_metrics_port": 15002,
        "agentgateway_docker_otlp_endpoint": "http://otel.example.com:4318",
        "agentgateway_docker_trace_sampling": "true",
        "agentgateway_docker_proxy_port": 15000,
        "agentgateway_docker_mcp_route_auth_mode": ROLE_DEFAULTS["agentgateway_docker_mcp_route_auth_mode"],
        "agentgateway_docker_mcp_targets": [
            {
                "name": "splunk",
                "host": "https://splunk.example.com/services/mcp",
                "tls_insecure": True,
            },
            {
                "name": "context7",
                "host": "https://context7.example.com/mcp",
                "tls_insecure": False,
            },
        ],
        "agentgateway_docker_qdrant_mcp_enabled": True,
        "agentgateway_docker_qdrant_memory_mcp_api_key": "example-memory-key",
        "agentgateway_docker_qdrant_mcp_port": 8000,
        "agentgateway_docker_docs_mcp_enabled": True,
        "agentgateway_docker_qdrant_api_key": "example-docs-key",
        "agentgateway_docker_docs_mcp_port": 8001,
        "agentgateway_docker_vikunja_mcp_enabled": True,
        "agentgateway_docker_vikunja_read_token": "example-vikunja-token",
        "agentgateway_docker_vikunja_mcp_port": 8002,
        "agentgateway_docker_nautobot_mcp_enabled": True,
        "agentgateway_docker_nautobot_read_token": "example-nautobot-token",
        "agentgateway_docker_nautobot_mcp_port": 8003,
        "agentgateway_docker_nautobot_openapi_enabled": False,
        "agentgateway_docker_a2a_targets": [
            {
                "name": "langgraph",
                "path_prefix": "/a2a/test/",
                "host": "http://langgraph.example.com:5000",
            }
        ],
        "ai_agents": roster,
        "ai_agent_owner": owners or {},
        "agentgateway_docker_roster_keys": roster_keys,
        "agentgateway_docker_hindsight_roster_tiers": roster_tiers,
        **(extra or {}),
    }
    return ENV.from_string(TEMPLATE_SOURCE if template_source is None else template_source).render(**context)


def _routes(rendered: str) -> list[dict[str, Any]]:
    config = yaml.safe_load(rendered)
    assert isinstance(config, dict)
    return config["binds"][0]["listeners"][0]["routes"]


def _strip_default_mcp_route_auth(rendered: str) -> str:
    """Remove only the four additive empty-roster policies from the old golden config."""
    routes = _routes(rendered)
    for path in MCP_ROUTE_PATHS:
        policy = _route_for_path(routes, path)["policies"]
        assert set(policy) == {"apiKey"}
        api_key = policy["apiKey"]
        assert api_key["mode"] == "optional"
        assert api_key["location"] == {"header": {"name": "X-Agent-Key"}}
        assert not api_key["keys"]

    normalized = rendered
    for path in MCP_ROUTE_PATHS:
        pattern = re.compile(
            rf"(?ms)(^    - matches:\n      - path:\n          exact: {re.escape(path)}\n)"
            r"      policies:\n.*?(?=      backends:\n)"
        )
        normalized, count = pattern.subn(r"\1", normalized, count=1)
        assert count == 1
    return normalized


def _route_for_path(routes: list[dict[str, Any]], path: str) -> dict[str, Any]:
    return next(route for route in routes if route["matches"][0]["path"].get("exact") == path)


@pytest.fixture(scope="module")
def baseline() -> str:
    return _strip_default_mcp_route_auth(_render())


def test_empty_roster_renders_the_existing_config_byte_for_byte(baseline: str) -> None:
    assert hashlib.sha256(baseline.encode()).hexdigest() == BASELINE_SHA256


def test_owned_route_requires_a_key_and_allows_only_its_owner(baseline: str) -> None:
    baseline_routes = _routes(baseline)
    routes = _routes(_render(AGENTS, {"splunk": "splunk-heimdall"}, KEYS))
    splunk = _route_for_path(routes, "/splunk")
    baseline_splunk = _route_for_path(baseline_routes, "/splunk")
    for path in MCP_ROUTE_PATHS:
        _route_for_path(routes, path).pop("policies")

    assert {key: value for key, value in splunk.items() if key != "policies"} == baseline_splunk
    policy = splunk["policies"]
    assert policy["apiKey"] == {
        "mode": "strict",
        "location": {"header": {"name": "X-Agent-Key"}},
        "keys": [
            {"key": KEYS["other_agent_agentgateway_key"], "metadata": {"agent": "other-agent"}},
            {"key": KEYS["splunk_heimdall_agentgateway_key"], "metadata": {"agent": "splunk-heimdall"}},
        ],
    }
    assert policy["authorization"] == {
        "rules": [{"allow": "apiKey.agent == 'splunk-heimdall'"}],
    }
    assert [route for route in routes if route is not splunk] == [
        route for route in baseline_routes if route is not baseline_splunk
    ]


def test_mcp_routes_validate_keys_when_present_by_default() -> None:
    assert ROLE_DEFAULTS["agentgateway_docker_mcp_route_auth_mode"] == "optional"
    routes = _routes(_render(AGENTS, {"splunk": "splunk-heimdall"}, KEYS))
    shared_keys = _route_for_path(routes, "/splunk")["policies"]["apiKey"]["keys"]

    for path in MCP_ROUTE_PATHS:
        api_key = _route_for_path(routes, path)["policies"]["apiKey"]
        assert api_key["mode"] == "optional"
        assert api_key["location"] == {"header": {"name": "X-Agent-Key"}}
        assert api_key["keys"] == shared_keys


def test_mcp_routes_render_strict_mode_when_selected() -> None:
    routes = _routes(
        _render(
            AGENTS,
            {"splunk": "splunk-heimdall"},
            KEYS,
            extra={"agentgateway_docker_mcp_route_auth_mode": "strict"},
        )
    )

    for path in MCP_ROUTE_PATHS:
        api_key = _route_for_path(routes, path)["policies"]["apiKey"]
        assert api_key["mode"] == "strict"
        assert api_key["location"] == {"header": {"name": "X-Agent-Key"}}


def test_staged_auth_mode_does_not_change_owned_hindsight_or_a2a_routes() -> None:
    from hindsight_acl_support import AGENTS as HINDSIGHT_AGENTS, KEYS as HINDSIGHT_KEYS, _hindsight_context

    extra = _hindsight_context()
    default_routes = _routes(
        _render(HINDSIGHT_AGENTS, {"splunk": "hermes-brain"}, HINDSIGHT_KEYS, extra=extra)
    )
    strict_routes = _routes(
        _render(
            HINDSIGHT_AGENTS,
            {"splunk": "hermes-brain"},
            HINDSIGHT_KEYS,
            extra={**extra, "agentgateway_docker_mcp_route_auth_mode": "strict"},
        )
    )

    assert _route_for_path(default_routes, "/splunk") == _route_for_path(strict_routes, "/splunk")
    default_hindsight = next(
        route
        for route in default_routes
        if route["matches"][0]["path"].get("pathPrefix") == "/hindsight"
        and route["policies"]["apiKey"]["location"]["header"]["name"] == "X-Agent-Key"
    )
    strict_hindsight = next(
        route
        for route in strict_routes
        if route["matches"][0]["path"].get("pathPrefix") == "/hindsight"
        and route["policies"]["apiKey"]["location"]["header"]["name"] == "X-Agent-Key"
    )
    assert default_hindsight == strict_hindsight
    default_a2a = next(
        route
        for route in default_routes
        if route["matches"][0]["path"].get("pathPrefix") == "/a2a/test/"
    )
    strict_a2a = next(
        route
        for route in strict_routes
        if route["matches"][0]["path"].get("pathPrefix") == "/a2a/test/"
    )
    assert default_a2a == strict_a2a


def test_nautobot_openapi_route_uses_the_staged_auth_mode() -> None:
    route_context = {
        "agentgateway_docker_nautobot_openapi_enabled": True,
        "agentgateway_docker_nautobot_openapi_schema_path": "/tmp/nautobot-openapi.json",
        "agentgateway_docker_nautobot_openapi_token_path": "/tmp/nautobot-token",
        "agentgateway_docker_nautobot_url": "",
    }
    for mode in ("optional", "strict"):
        routes = _routes(
            _render(
                AGENTS,
                {},
                KEYS,
                extra={**route_context, "agentgateway_docker_mcp_route_auth_mode": mode},
            )
        )
        nautobot = _route_for_path(routes, "/nautobot")
        assert nautobot["policies"]["apiKey"]["mode"] == mode
        assert nautobot["policies"]["apiKey"]["location"] == {"header": {"name": "X-Agent-Key"}}


def test_missing_roster_key_fails_the_role_assert_expression() -> None:
    tasks = yaml.safe_load(TASKS.read_text())
    validation = next(
        task
        for task in tasks
        if task.get("name") == "Validate that every roster agent has a gateway API key"
    )
    condition = validation["ansible.builtin.assert"]["that"][0]
    roster_keys = _render_map(
        ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
        ai_agent_gateway_key_fields=_render_key_fields(AGENTS),
        openbao_secrets_by_domain={
            "agentgateway-agent-keys": {
                "splunk_heimdall_agentgateway_key": KEYS["splunk_heimdall_agentgateway_key"]
            }
        },
    )
    assert ENV.compile_expression(condition)(item={"value": roster_keys["splunk-heimdall"]}) is True
    assert ENV.compile_expression(condition)(item={"value": roster_keys["other-agent"]}) is False


def test_hermes_a2a_target_uses_the_published_container_and_port_contract() -> None:
    targets = ROLE_DEFAULTS["agentgateway_docker_a2a_targets"]
    assert [target["name"] for target in targets] == ["hermes", "langgraph"]
    hermes = targets[0]
    assert hermes["path_prefix"] == "/a2a/hermes/"
    assert "tofu_data']['containers']['hermes-agent']['ip" in hermes["host"]
    assert "tofu_data']['constants']['service_ports']['hermes_a2a" in hermes["host"]
    assert hermes["bearer_token"] == "{{ agentgateway_docker_hermes_a2a_token }}"
    assert "HERMES_A2A_TOKEN" in ROLE_DEFAULTS["agentgateway_docker_hermes_a2a_token"]
    assert "hermes_a2a_token" in ROLE_DEFAULTS["agentgateway_docker_hermes_a2a_token"]


def test_hermes_a2a_route_authenticates_callers_and_allows_only_trusted_tier() -> None:
    agents = {
        "hermes-brain": {"trust_tier": "trusted"},
        "paperclip": {"trust_tier": "untrusted"},
        "no-tier-agent": {},
    }
    keys = {
        "hermes_brain_agentgateway_key": "fixture-hermes-caller-key",
        "paperclip_agentgateway_key": "fixture-paperclip-caller-key",
        "no_tier_agent_agentgateway_key": "fixture-no-tier-caller-key",
    }
    a2a_targets = [
        {
            "name": "hermes",
            "path_prefix": "/a2a/hermes/",
            "host": "hermes-agent.example.test:9901",
            "bearer_token": "fixture-hermes-upstream-token",
        },
        {
            "name": "langgraph",
            "path_prefix": "/a2a/",
            "host": "langgraph.example.test:5000",
        },
    ]
    routes = _routes(
        _render(
            agents=agents,
            keys=keys,
            extra={
                "agentgateway_docker_a2a_targets": a2a_targets,
                "agentgateway_docker_a2a_allowed_tier": "trusted",
            },
        )
    )
    a2a_routes = [
        route for route in routes
        if route.get("policies", {}).get("a2a") == {}
    ]
    hermes = a2a_routes[0]
    langgraph = a2a_routes[1]

    assert routes.index(hermes) < routes.index(langgraph)
    assert hermes["matches"][0]["path"] == {"pathPrefix": "/a2a/hermes/"}
    assert hermes["policies"]["apiKey"]["mode"] == "strict"
    assert hermes["policies"]["apiKey"]["location"] == {
        "header": {"name": "X-Agent-Key"}
    }
    metadata = {
        entry["metadata"]["agent"]: entry["metadata"]
        for entry in hermes["policies"]["apiKey"]["keys"]
    }
    assert metadata == {
        "hermes-brain": {"agent": "hermes-brain", "tier": "trusted"},
        "paperclip": {"agent": "paperclip", "tier": "untrusted"},
        "no-tier-agent": {"agent": "no-tier-agent"},
    }
    rules = hermes["policies"]["authorization"]["rules"]
    assert rules == [{"allow": 'apiKey.tier == "trusted"'}]

    def allowed(agent: str | None) -> bool:
        request: dict[str, Any] = {
            "request": {"method": "POST", "path": "/a2a/hermes/"}
        }
        if agent is not None:
            request["apiKey"] = metadata[agent]
        return authorize(rules, request)[0]

    assert allowed("hermes-brain")
    assert not allowed("paperclip")
    assert not allowed("no-tier-agent")
    assert not allowed(None)

    assert hermes["backends"][0] == {
        "host": "hermes-agent.example.test:9901",
        "policies": {
            "backendAuth": {
                "key": {
                    "value": "fixture-hermes-upstream-token",
                    "location": {
                        "header": {"name": "authorization", "prefix": "Bearer "}
                    },
                }
            }
        },
    }
    assert langgraph["matches"][0]["path"] == {"pathPrefix": "/a2a/"}
    assert langgraph["policies"] == {"a2a": {}}
