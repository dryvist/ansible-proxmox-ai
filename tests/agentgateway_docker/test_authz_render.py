"""Per-agent keys protect owned agentgateway routes and staged MCP auth."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml
from ansible.plugins.filter.core import FilterModule

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = REPO_ROOT / "inventory/group_vars/all/00-core.yml"
DEFAULTS = REPO_ROOT / "roles/agentgateway_docker/defaults/main/00-gateway.yml"
MCP_ROUTE_AUTH_DEFAULTS = REPO_ROOT / "roles/agentgateway_docker/defaults/main/30-mcp-route-auth.yml"
TASKS = REPO_ROOT / "roles/agentgateway_docker/tasks/main.yml"
TEMPLATE = REPO_ROOT / "roles/agentgateway_docker/templates/config.yaml.j2"
# The shared-route auth policies are normalized below so this remains a check of
# the rest of the config render. Facade defaults and route identity are covered
# in test_facade_route.py.
BASELINE_SHA256 = "ca78c630817e07bc5c427cac4ce2feab36de4f7cb110e4068a655616b3c41e3d"
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
FACADE_DEFAULTS = yaml.safe_load((DEFAULTS.parent / "40-facade.yml").read_text())
ROLE_DEFAULTS = {
    **yaml.safe_load(DEFAULTS.read_text()),
    **yaml.safe_load(MCP_ROUTE_AUTH_DEFAULTS.read_text()),
    **yaml.safe_load((DEFAULTS.parent / "05-a2a-targets.yml").read_text()),
    **FACADE_DEFAULTS,
}
HINDSIGHT_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles/agentgateway_docker/defaults/main/20-hindsight-acl.yml").read_text()
)
TEMPLATE_SOURCE = TEMPLATE.read_text()


def _render_map(expression: str, **context: Any) -> dict[str, Any]:
    return yaml.safe_load(ENV.from_string(expression).render(**context))


def _render_callers(roster: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """`ai_callers` as inventory/group_vars/all/00-core.yml derives it: registry plus roster."""
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
        **FACADE_DEFAULTS,
        # Keep generic authorization fixtures independent of the role's live
        # facade default; its enabled-by-default case is tested separately.
        "agentgateway_docker_facade_enabled": False,
        "agentgateway_docker_facade_callers": [],
        **(extra or {}),
    }
    return ENV.from_string(TEMPLATE_SOURCE if template_source is None else template_source).render(**context)


def _routes(rendered: str) -> list[dict[str, Any]]:
    config = yaml.safe_load(rendered)
    assert isinstance(config, dict)
    return config["binds"][0]["listeners"][0]["routes"]


def _strip_default_mcp_route_auth(rendered: str) -> str:
    """Remove shared-route policies so the baseline covers the remaining render."""
    routes = _routes(rendered)
    for path in MCP_ROUTE_PATHS:
        policy = _route_for_path(routes, path)["policies"]
        expected = {"apiKey", "requestHeaderModifier"} if path == "/vikunja" else {"apiKey"}
        assert set(policy) == expected
        api_key = policy["apiKey"]
        assert api_key["mode"] == "optional"
        assert api_key["location"] == {"header": {"name": "X-Agent-Key"}}
        assert not api_key["keys"]
        if path == "/vikunja":
            assert policy["requestHeaderModifier"]["remove"] == FACADE_DEFAULTS[
                "agentgateway_docker_caller_credential_headers"
            ]

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
    from hindsight_acl_support import AGENTS as HINDSIGHT_AGENTS
    from hindsight_acl_support import KEYS as HINDSIGHT_KEYS
    from hindsight_acl_support import _hindsight_context

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
    assert (
        ENV.compile_expression(condition)(
            item={"key": "splunk-heimdall"}, agentgateway_docker_roster_keys=roster_keys
        )
        is True
    )
    assert (
        ENV.compile_expression(condition)(
            item={"key": "other-agent"}, agentgateway_docker_roster_keys=roster_keys
        )
        is False
    )


def _rendered_caller_keys(rendered: str) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = []
    for route in _routes(rendered):
        keys.extend(route.get("policies", {}).get("apiKey", {}).get("keys", []))
    return keys


def test_keyless_callers_are_omitted_from_rendered_config() -> None:
    # other-agent holds only whitespace and the registry caller has no leaf: both are keyless.
    keys = {
        "splunk_heimdall_agentgateway_key": KEYS["splunk_heimdall_agentgateway_key"],
        "other_agent_agentgateway_key": "   ",
    }
    rendered = _render(
        AGENTS,
        {},
        keys,
        registry={"registry-unseeded": {"trust_tier": "untrusted"}},
    )
    rendered_keys = _rendered_caller_keys(rendered)

    assert rendered_keys
    assert {key["metadata"]["agent"] for key in rendered_keys} == {"splunk-heimdall"}
    assert all(key["key"] for key in rendered_keys)
    assert 'key: ""' not in rendered


def test_gateway_reader_domain_is_required_when_gateway_callers_exist() -> None:
    tasks = yaml.safe_load(TASKS.read_text())
    presence = next(
        task
        for task in tasks
        if task.get("name") == "Require the agentgateway-agent-keys domain when gateway callers exist"
    )
    condition = presence["ansible.builtin.assert"]["that"][0]
    when = presence["when"][0]

    assert ENV.compile_expression(condition)(openbao_secrets_by_domain={}) is False
    assert (
        ENV.compile_expression(condition)(openbao_secrets_by_domain={"agentgateway-agent-keys": {}})
        is True
    )
    callers = {"splunk-heimdall": "splunk_heimdall_agentgateway_key"}
    assert ENV.compile_expression(when)(ai_agent_gateway_key_fields=callers) is True
    assert ENV.compile_expression(when)(ai_agent_gateway_key_fields={}) is False
