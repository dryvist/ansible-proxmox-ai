"""Hermes A2A route rendering through agentgateway."""

from __future__ import annotations

from typing import Any
from cel_subset import authorize
import jinja2
import pytest
import yaml
from test_authz_render import ENV, ROLE_DEFAULTS, _render, _routes


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


def test_default_peer_roster_preserves_registered_a2a_callers() -> None:
    agents = {
        "trusted-agent": {"trust_tier": "trusted"},
        "untrusted-agent": {"trust_tier": "untrusted"},
        "no-tier-agent": {},
    }
    registry = {"claude-code": {"trust_tier": "trusted"}, "unseeded": {"trust_tier": "trusted"}}
    keys = {f"{name.replace('-', '_')}_agentgateway_key": f"fixture-{name}" for name in [*agents, "claude-code"]}
    routes = _routes(_render(agents=agents, keys=keys, registry=registry, extra={
        "agentgateway_docker_a2a_targets": [
            {"name": name, "path_prefix": f"/a2a/{name}/", "host": f"{name}.example.test:8000"}
            for name in ("hermes", "langgraph")
        ],
    }))
    a2a_routes = [route for route in routes if "a2a" in route.get("policies", {})]
    for route, allowed in zip(a2a_routes, ({"trusted-agent", "claude-code"}, {*agents, "claude-code"})):
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


ROSTER: dict[str, Any] = {
    "brain": {"trust_tier": "trusted", "a2a_url": "http://brain.example.test:9901/"},
    "graph": {"a2a_url": "http://graph.example.test:5000/a2a/", "a2a_callers": ["trusted-agent", "unkeyed"]},
    "plain": {"trust_tier": "trusted"},
    "hermes": {"a2a_url": "http://shadow.example.test:1/"},
}


def _derived(roster: dict[str, Any], env: dict[str, str] | None = None,
             secrets: dict[str, str] | None = None) -> Any:
    rendered = ENV.from_string(ROLE_DEFAULTS["agentgateway_docker_roster_a2a_targets"]).render(
        ai_agents=roster,
        agentgateway_docker_a2a_targets=ROLE_DEFAULTS["agentgateway_docker_a2a_targets"],
        bao_apps_secrets=secrets or {},
        lookup=lambda _plugin, key: (env or {}).get(key, ""),
    )
    return yaml.safe_load(rendered)


def test_roster_agents_with_a2a_url_get_routes_and_static_names_win() -> None:
    targets = _derived(ROSTER, env={"BRAIN_A2A_TOKEN": "fixture-env-token"}, secrets={"graph_a2a_token": "fixture-kv"})
    assert targets == [
        {"name": "brain", "path_prefix": "/a2a/brain/", "host": "brain.example.test:9901",
         "backend_path_prefix": "/", "bearer_token": "fixture-env-token"},
        {"name": "graph", "path_prefix": "/a2a/graph/", "host": "graph.example.test:5000",
         "backend_path_prefix": "/a2a/", "bearer_token": "fixture-kv"},
    ]


def test_static_routes_stay_and_derived_routes_follow_with_their_callers() -> None:
    agents = {"trusted-agent": {"trust_tier": "trusted"}, "untrusted-agent": {"trust_tier": "untrusted"}}
    keys = {f"{name.replace('-', '_')}_agentgateway_key": f"fixture-{name}" for name in agents}
    derived = _derived(ROSTER)
    routes = _routes(_render(agents={**agents, **ROSTER}, keys=keys, extra={
        "agentgateway_docker_a2a_targets": [
            {"name": name, "path_prefix": f"/a2a/{name}/", "host": f"{name}.example.test:8000"}
            for name in ("hermes", "langgraph")
        ],
        "agentgateway_docker_roster_a2a_targets": derived,
    }))
    a2a = {route["matches"][0]["path"]["pathPrefix"]: route for route in routes if "a2a" in route.get("policies", {})}
    assert list(a2a) == ["/a2a/hermes/", "/a2a/langgraph/", "/a2a/brain/", "/a2a/graph/"]
    graph_rules = a2a["/a2a/graph/"]["policies"]["authorization"]["rules"]
    brain_rules = a2a["/a2a/brain/"]["policies"]["authorization"]["rules"]
    assert graph_rules == [{"allow": 'apiKey.agent in ["trusted-agent"]'}]
    assert brain_rules == [{"allow": 'apiKey.agent in ["trusted-agent"]'}]


def test_an_agent_without_a_token_gets_a_route_without_a_backend_credential() -> None:
    assert "bearer_token" not in _derived({"graph": ROSTER["graph"]})[0]


def test_a_url_without_http_fails_the_render() -> None:
    with pytest.raises(jinja2.exceptions.UndefinedError, match="a2a_url must be an http:// URL"):
        _derived({"brain": {"a2a_url": "https://brain.example.test/"}})
