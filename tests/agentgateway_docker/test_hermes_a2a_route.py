"""A2A routes derived from the roster and rendered through agentgateway."""

from __future__ import annotations

from typing import Any

import jinja2
import pytest
import yaml
from cel_subset import authorize
from test_authz_render import ENV, ROLE_DEFAULTS, _render, _routes

ROSTER: dict[str, Any] = {
    "brain": {"trust_tier": "trusted", "a2a_url": "http://brain.example.test:9901/"},
    "graph": {"a2a_url": "http://graph.example.test:5000/a2a/"},
    "plain": {"trust_tier": "trusted"},
}


def _targets(roster: dict[str, Any], env: dict[str, str] | None = None, secrets: dict[str, str] | None = None) -> Any:
    rendered = ENV.from_string(ROLE_DEFAULTS["agentgateway_docker_a2a_targets"]).render(
        ai_agents=roster,
        bao_apps_secrets=secrets or {},
        lookup=lambda _plugin, key: (env or {}).get(key, ""),
    )
    return yaml.safe_load(rendered)


def test_every_roster_agent_that_declares_a2a_gets_a_route() -> None:
    targets = _targets(ROSTER, env={"BRAIN_A2A_TOKEN": "fixture-env-token"}, secrets={"graph_a2a_token": "fixture-kv"})
    assert targets == [
        {"name": "brain", "path_prefix": "/a2a/brain/", "host": "brain.example.test:9901",
         "backend_path_prefix": "/", "bearer_token": "fixture-env-token"},
        {"name": "graph", "path_prefix": "/a2a/graph/", "host": "graph.example.test:5000",
         "backend_path_prefix": "/a2a/", "bearer_token": "fixture-kv"},
    ]
    routes = _routes(_render(extra={"agentgateway_docker_a2a_targets": targets}))
    a2a_prefixes = {route["matches"][0]["path"]["pathPrefix"] for route in routes if "a2a" in route.get("policies", {})}
    declared = {f"/a2a/{name}/" for name, agent in ROSTER.items() if "a2a_url" in agent}
    assert a2a_prefixes == declared, f"roster agents with a2a_url but no route: {declared - a2a_prefixes}"


def test_an_agent_without_a_token_gets_a_route_without_a_backend_credential() -> None:
    assert "bearer_token" not in _targets({"graph": ROSTER["graph"]})[0]


def test_a_url_without_http_fails_the_render() -> None:
    with pytest.raises(jinja2.exceptions.UndefinedError, match="a2a_url must be an http:// URL"):
        _targets({"brain": {"a2a_url": "https://brain.example.test/"}})


def test_default_peer_roster_preserves_registered_a2a_callers() -> None:
    agents: dict[str, Any] = {
        "trusted-agent": {"trust_tier": "trusted"},
        "untrusted-agent": {"trust_tier": "untrusted"},
        "no-tier-agent": {},
    }
    # langgraph declares its callers; hermes declares none and falls back to the trusted tier.
    agents["langgraph"] = {"a2a_callers": [*agents, "claude-code", "unseeded"]}
    registry = {"claude-code": {"trust_tier": "trusted"}, "unseeded": {"trust_tier": "trusted"}}
    keys = {f"{name.replace('-', '_')}_agentgateway_key": f"fixture-{name}" for name in [*agents, "claude-code"]}
    routes = _routes(_render(agents=agents, keys=keys, registry=registry, extra={
        "agentgateway_docker_a2a_targets": [
            {"name": name, "path_prefix": f"/a2a/{name}/", "host": f"{name}.example.test:8000"}
            for name in ("hermes", "langgraph")
        ],
    }))
    a2a_routes = [route for route in routes if "a2a" in route.get("policies", {})]
    keyed = {"trusted-agent", "untrusted-agent", "no-tier-agent", "claude-code"}
    for route, allowed in zip(a2a_routes, ({"trusted-agent", "claude-code"}, keyed), strict=True):
        policies = route["policies"]
        assert policies["apiKey"]["mode"] == "strict"
        rules = policies["authorization"]["rules"]
        for caller in [*agents, *registry, "unknown"]:
            assert authorize(rules, {"apiKey": {"agent": caller}})[0] is (caller in allowed)
        assert not authorize(rules, {})[0]


def test_a2a_routes_authenticate_callers_and_use_peer_allowlists() -> None:
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
            "path_prefix": "/a2a/langgraph/",
            "backend_path_prefix": "/a2a/",
            "host": "langgraph.example.test:5000",
        },
    ]
    routes = _routes(
        _render(
            agents=agents,
            keys=keys,
            extra={
                "agentgateway_docker_a2a_targets": a2a_targets,
                "agentgateway_docker_agent_roster": [
                    {"agent": "hermes", "allowed_callers": ["hermes-brain"]},
                    {"agent": "langgraph", "allowed_callers": ["paperclip"]},
                ],
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
    assert langgraph["policies"]["urlRewrite"] == {"path": {"prefix": "/a2a/"}}
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
    assert rules == [{"allow": 'apiKey.agent in ["hermes-brain"]'}]

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
    assert langgraph["matches"][0]["path"] == {"pathPrefix": "/a2a/langgraph/"}
    assert langgraph["policies"]["apiKey"]["mode"] == "strict"
    langgraph_rules = langgraph["policies"]["authorization"]["rules"]
    assert authorize(langgraph_rules, {"apiKey": metadata["paperclip"]})[0]
    assert not authorize(langgraph_rules, {"apiKey": metadata["hermes-brain"]})[0]
    assert not authorize(langgraph_rules, {})[0]
