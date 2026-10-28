"""Hermes A2A route rendering through agentgateway."""

from __future__ import annotations

from typing import Any
from cel_subset import authorize
from test_authz_render import ROLE_DEFAULTS, _render, _routes


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
    assert hermes["policies"]["urlRewrite"] == {"path": {"prefix": "/"}}
    assert "urlRewrite" not in langgraph["policies"]
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
