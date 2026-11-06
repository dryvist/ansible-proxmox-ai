"""Named agent routes and exclusive integration ownership."""

from __future__ import annotations

import pytest
from cel_subset import authorize
from test_authz_render import AGENTS, KEYS, _render, _route_for_path, _routes
from test_facade_route import CREDENTIAL_HEADERS, _task, _task_passes


@pytest.mark.parametrize("peers", [[], [{"agent": "clerk", "allowed_callers": []}]])
def test_missing_or_empty_peer_allowlists_deny_every_caller(peers: list) -> None:
    routes = _routes(_render(AGENTS, {}, KEYS, extra={
        "agentgateway_docker_dify_mcp_targets": [
            {"name": "clerk", "url": "https://dify.example.test/mcp/app"},
        ],
        "agentgateway_docker_agent_roster": peers,
    }))
    policies = _route_for_path(routes, "/agents/clerk")["policies"]
    assert policies["apiKey"]["mode"] == "strict"
    assert policies["authorization"]["rules"] == [{"allow": "apiKey.agent in []"}]
    assert not authorize(policies["authorization"]["rules"], {"apiKey": {"agent": "other-agent"}})[0]
    assert not authorize(policies["authorization"]["rules"], {})[0]


def test_agent_routes_without_seeded_keys_render_an_empty_key_array() -> None:
    routes = _routes(_render(extra={
        "agentgateway_docker_dify_mcp_targets": [
            {"name": "clerk", "url": "https://dify.example.test/mcp/app"},
        ],
    }))
    assert _route_for_path(routes, "/agents/clerk")["policies"]["apiKey"]["keys"] == []


def test_dify_agent_routes_use_distinct_urls_and_authenticated_peer_names() -> None:
    targets = [
        {"name": "clerk", "url": "https://dify.example.test/mcp/clerk"},
        {"name": "triage", "url": "https://dify.example.test/mcp/triage"},
    ]
    routes = _routes(_render(AGENTS, {}, KEYS, extra={
        "agentgateway_docker_dify_mcp_targets": targets,
        "agentgateway_docker_agent_roster": [
            {"agent": "clerk", "allowed_callers": ["other-agent"]},
        ],
    }))
    clerk = _route_for_path(routes, "/agents/clerk")
    assert clerk["backends"] == [{"mcp": {"targets": [
        {"name": "clerk", "mcp": {"host": targets[0]["url"]}},
    ]}}]
    policies = clerk["policies"]
    assert policies["apiKey"]["location"] == {"header": {"name": "X-Agent-Key"}}
    assert policies["requestHeaderModifier"] == {"remove": CREDENTIAL_HEADERS}
    rules = policies["authorization"]["rules"]
    assert authorize(rules, {"apiKey": {"agent": "other-agent"}})[0]
    assert not authorize(rules, {"apiKey": {"agent": "splunk-heimdall"}})[0]
    assert not authorize(rules, {})[0]
    triage = _route_for_path(routes, "/agents/triage")
    assert triage["backends"][0]["mcp"]["targets"][0]["mcp"]["host"] == targets[1]["url"]
    assert not authorize(triage["policies"]["authorization"]["rules"], {"apiKey": {"agent": "other-agent"}})[0]


def test_connector_ownership_is_exclusive() -> None:
    task = _task("Validate exclusive connector ownership")
    assert _task_passes(task, ai_agents=AGENTS)
    assert not _task_passes(task, ai_agents={
        "connector": {"owns": ["integration"]},
        "other-agent": {"owns": ["integration"]},
    })


def test_integration_route_allows_only_its_connector() -> None:
    route = _route_for_path(_routes(_render(AGENTS, {"splunk": "splunk-heimdall"}, KEYS)), "/splunk")
    rules = route["policies"]["authorization"]["rules"]
    # The existing owner policy uses CEL single-quoted strings; the test
    # evaluator accepts JSON string literals, so normalize only the quoting.
    normalized = [{"allow": rules[0]["allow"].replace("'", '"')}]
    assert authorize(normalized, {"apiKey": {"agent": "splunk-heimdall"}})[0]
    assert not authorize(normalized, {"apiKey": {"agent": "other-agent"}})[0]
    assert not authorize(normalized, {})[0]


@pytest.mark.parametrize("names, expected", [(["clerk", "triage"], True), (["clerk", "clerk"], False), (["../clerk"], False)])
def test_agent_route_names_are_unique_and_path_safe(names: list[str], expected: bool) -> None:
    assert _task_passes(_task("Validate agent route names"),
        agentgateway_docker_a2a_targets=[],
        agentgateway_docker_dify_mcp_targets=[{"name": name} for name in names],
        agentgateway_docker_agent_roster=[],
    ) is expected


@pytest.mark.parametrize("callers, expected", [(["other-agent"], True), ([], True), (["unknown"], False), ("other-agent", False)])
def test_peer_allowlists_require_authenticated_callers(callers: list[str] | str, expected: bool) -> None:
    assert _task_passes(_task("Validate agent peer allowlists"),
        agentgateway_docker_agent_roster=[{"agent": "clerk", "allowed_callers": callers}],
        agentgateway_docker_roster_keys={"other-agent": "fixture-caller-key"},
    ) is expected


@pytest.mark.parametrize("url, expected", [
    ("https://dify.example.test/mcp/app", True),
    ("http://dify.example.test/mcp/app", True),
    ("https://dify.example.test/", False),
    ("file:///mcp/app", False),
])
def test_dify_targets_require_http_urls_with_app_paths(url: str, expected: bool) -> None:
    assert _task_passes(_task("Validate Dify MCP target URLs"),
        agentgateway_docker_dify_mcp_targets=[{"name": "clerk", "url": url}],
    ) is expected
