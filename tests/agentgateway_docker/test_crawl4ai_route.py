"""The /crawl4ai page-reader MCP route: URL+SSE upstream, gateway-held bearer,
`md`-only tool allowlist, and a closed caller allowlist."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from cel_subset import authorize
from test_authz_render import ROLE_DEFAULTS, TASKS, _render, _route_for_path, _routes

URL = "https://crawl4ai.example.com/mcp/sse"
TOKEN = "example-crawl4ai-token"
CALLER = "claude-code"
CREDENTIAL_HEADERS = ROLE_DEFAULTS["agentgateway_docker_caller_credential_headers"]
ALLOWED_TOOLS = ("md",)
DENIED_TOOLS = ("html", "screenshot", "pdf", "execute_js", "crawl", "ask")
ON = {
    "agentgateway_docker_crawl4ai_mcp_enabled": True,
    "agentgateway_docker_crawl4ai_mcp_url": URL,
    "agentgateway_docker_crawl4ai_api_token": TOKEN,
    "agentgateway_docker_crawl4ai_allowed_callers": [CALLER],
}


def _exact_paths(rendered: str) -> list[str]:
    return [
        route["matches"][0]["path"].get("exact")
        for route in _routes(rendered)
        if "path" in route["matches"][0]
    ]


def _crawl4ai_route(**extra: Any) -> dict[str, Any]:
    return _route_for_path(_routes(_render(extra={**ON, **extra})), "/crawl4ai")


def test_route_inputs_default_off_and_empty() -> None:
    assert ROLE_DEFAULTS["agentgateway_docker_crawl4ai_mcp_enabled"] is False
    assert ROLE_DEFAULTS["agentgateway_docker_crawl4ai_mcp_url"] == ""
    assert ROLE_DEFAULTS["agentgateway_docker_crawl4ai_api_token"] == (
        "{{ lookup('env', 'CRAWL4AI_API_TOKEN') }}"
    )
    # Defaults keep the whole-config render byte-for-byte unchanged: the
    # disabled route block emits nothing (pinned by BASELINE_SHA256 in
    # test_authz_render.py) and no /crawl4ai route exists.
    assert "/crawl4ai" not in _exact_paths(_render())


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"agentgateway_docker_crawl4ai_mcp_enabled": True},
        {
            "agentgateway_docker_crawl4ai_mcp_enabled": True,
            "agentgateway_docker_crawl4ai_mcp_url": URL,
        },
        {
            "agentgateway_docker_crawl4ai_mcp_enabled": True,
            "agentgateway_docker_crawl4ai_api_token": TOKEN,
        },
    ],
)
def test_route_renders_only_with_url_and_token_together(overrides: dict[str, Any]) -> None:
    # Each case omits at least one required input (url or token) or leaves the
    # route disabled: the /crawl4ai route must not render in any of them.
    assert "/crawl4ai" not in _exact_paths(_render(extra=overrides))


def test_enabled_route_dials_the_sse_url_with_the_gateway_held_bearer() -> None:
    route = _crawl4ai_route()
    target = route["backends"][0]["mcp"]["targets"][0]

    assert target["name"] == "crawl4ai"
    assert target["sse"] == {"host": URL}
    assert target["policies"]["backendAuth"]["key"]["value"] == TOKEN
    assert target["policies"]["backendAuth"]["key"]["location"] == {
        "header": {"name": "authorization", "prefix": "Bearer "}
    }


def test_enabled_route_authenticates_callers_and_strips_their_credentials() -> None:
    policies = _crawl4ai_route()["policies"]

    assert set(policies) == {"apiKey", "authorization", "mcpAuthorization", "requestHeaderModifier"}
    assert policies["apiKey"]["mode"] == ROLE_DEFAULTS["agentgateway_docker_mcp_route_auth_mode"]
    assert policies["apiKey"]["location"] == {"header": {"name": "X-Agent-Key"}}
    assert policies["authorization"] == {"rules": [{"allow": f"apiKey.agent == '{CALLER}'"}]}
    assert policies["requestHeaderModifier"] == {"remove": CREDENTIAL_HEADERS}


def test_caller_allowlist_follows_the_variable_and_denies_unlisted_callers() -> None:
    callers = [CALLER, "scout-open"]
    rules = _crawl4ai_route(
        agentgateway_docker_crawl4ai_allowed_callers=callers
    )["policies"]["authorization"]["rules"]

    assert rules == [{"allow": f"apiKey.agent == '{caller}'"} for caller in callers]
    assert not any("ghost-agent" in rule["allow"] for rule in rules)


def test_tool_policy_admits_md_and_denies_every_other_crawl4ai_tool() -> None:
    rule_sets = _crawl4ai_route()["policies"]["mcpAuthorization"]["rules"]

    assert rule_sets == [{"allow": 'mcp.tool.name == "md"'}]
    for tool in ALLOWED_TOOLS:
        allowed, _ = authorize(rule_sets, {"mcp": {"tool": {"target": "crawl4ai", "name": tool}}})
        assert allowed, tool
    for tool in DENIED_TOOLS:
        allowed, _ = authorize(rule_sets, {"mcp": {"tool": {"target": "crawl4ai", "name": tool}}})
        assert not allowed, tool


def test_upstream_token_stays_out_of_rendered_task_output() -> None:
    # The config template embeds the token exactly once (the target
    # backendAuth value), and every task that renders token-bearing content
    # suppresses its output.
    rendered = _render(extra=ON)
    assert rendered.count(TOKEN) == 1
    tasks = yaml.safe_load(TASKS.read_text())
    for name in (
        "Deploy agentgateway config",
        "Deploy agentgateway docker-compose template",
    ):
        task = next(item for item in tasks if item.get("name") == name)
        assert task["no_log"] is True, name
    # No compose sidecar carries the credential: the Crawl4AI server is an
    # external upstream reached by URL, so the compose template (whose render
    # task is also no_log) has no crawl4ai service or environment to leak.
    compose_template = (
        TASKS.parent.parent / "templates" / "docker-compose.yml.j2"
    ).read_text()
    assert "crawl4ai" not in compose_template
