"""The Dify clerk gets a separate writable MCP route with task-only tools."""

from __future__ import annotations

from test_authz_render import _render, _route_for_path, _routes


def test_vikunja_clerk_route_targets_the_dedicated_http_sidecar() -> None:
    rendered = _render(
        extra={
            "agentgateway_docker_vikunja_clerk_enabled": True,
            "agentgateway_docker_vikunja_clerk_api_token": "fixture-api-token",
            "agentgateway_docker_vikunja_clerk_http_token": "x" * 64,
            "agentgateway_docker_vikunja_clerk_port": 8004,
        }
    )
    route = _route_for_path(_routes(rendered), "/vikunja-clerk")

    assert route["backends"][0]["mcp"]["targets"][0]["mcp"]["host"] == (
        "http://mcp-vikunja-clerk:8004/mcp"
    )
    assert "policies" not in route


def test_vikunja_clerk_route_is_omitted_without_both_credentials() -> None:
    rendered = _render(
        extra={
            "agentgateway_docker_vikunja_clerk_enabled": True,
            "agentgateway_docker_vikunja_clerk_api_token": "fixture-api-token",
            "agentgateway_docker_vikunja_clerk_http_token": "",
        }
    )

    assert "/vikunja-clerk" not in rendered
