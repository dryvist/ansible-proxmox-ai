"""Behavior assertions for the role-rendered gateway's native MCP routes."""

from __future__ import annotations

import json
from typing import Any

from gateway_falsifier_contract import (
    BAD_KEY,
    ERROR_URL,
    GATEWAY_URL,
    NONOWNER_KEY,
    OWNER_KEY,
    PROTOCOL_VERSION,
    SUCCESS_URL,
    VIKUNJA_CALLER_MARKERS,
)
from gateway_observer_helpers import gateway_config, stat, vikunja_route_bytes
from gateway_protocol_helpers import caller_headers, request, rpc, rpc_response


def test_gateway_fixture_auth_and_mcp_lifecycle(runtime: dict[str, Any]) -> None:
    socket_path = runtime["socket_path"]
    for key in (None, BAD_KEY, NONOWNER_KEY):
        marker = f"deny-agents-{key or 'missing'}"
        before = stat(socket_path, "http.mcp_observer.downstream_rq_total")
        response = request(
            GATEWAY_URL,
            "/agents",
            body=rpc(
                "initialize",
                1,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "denial-probe", "version": "1"},
                },
            ),
            headers=caller_headers(key, marker),
        )
        after = stat(socket_path, "http.mcp_observer.downstream_rq_total")
        assert response[0] in (401, 403), f"/agents did not reject {marker}"
        assert after == before, f"/agents forwarded denied request {marker} upstream"

    init = request(
        GATEWAY_URL,
        "/agents",
        body=rpc(
            "initialize",
            2,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gateway-falsifier", "version": "1"},
            },
        ),
        headers=caller_headers(OWNER_KEY, "facade-initialize"),
    )
    assert init[0] == 200 and init[1].get("mcp-session-id"), (
        "authorized facade initialize failed"
    )
    session_id = init[1]["mcp-session-id"]

    before = stat(socket_path, "http.mcp_observer.downstream_rq_total")
    denied = request(
        GATEWAY_URL,
        "/agents",
        body=rpc("tools/list", 3),
        headers=caller_headers(None, "facade-continuation-missing-key", session_id),
    )
    assert denied[0] in (401, 403), (
        "gateway accepted a continuation without its caller key"
    )
    assert stat(socket_path, "http.mcp_observer.downstream_rq_total") == before, (
        "keyless MCP continuation reached the backend"
    )

    initialized = request(
        GATEWAY_URL,
        "/agents",
        body=rpc("notifications/initialized", None),
        headers=caller_headers(OWNER_KEY, "facade-initialized", session_id),
    )
    assert initialized[0] in (200, 202, 204), (
        "authorized initialized notification failed"
    )

    listed = request(
        GATEWAY_URL,
        "/agents",
        body=rpc("tools/list", 4),
        headers=caller_headers(OWNER_KEY, "facade-tools-list", session_id),
    )
    listed_message = rpc_response(listed, 4)
    assert any(tool["name"] == "fetch" for tool in listed_message["result"]["tools"]), (
        "tools/list did not expose the fixture backend's fetch tool"
    )

    before = stat(socket_path, "http.mcp_observer.downstream_rq_total")
    call = request(
        GATEWAY_URL,
        "/agents",
        body=rpc("tools/call", 5, {"name": "fetch", "arguments": {"url": SUCCESS_URL}}),
        headers=caller_headers(OWNER_KEY, "facade-success-call", session_id),
    )
    result = rpc_response(call, 5).get("result", {})
    assert any(
        content.get("text") == f"fetched: {SUCCESS_URL}"
        for content in result.get("content", [])
    ), "successful fetch did not return the exact fixture result"
    assert stat(socket_path, "http.mcp_observer.downstream_rq_total") > before, (
        "successful tools/call produced no backend request"
    )

    sse_status, sse_headers, _ = request(
        GATEWAY_URL,
        "/agents",
        method="GET",
        headers={
            **caller_headers(OWNER_KEY, "facade-sse", session_id),
            "Accept": "text/event-stream",
        },
        close_after_headers=True,
    )
    assert sse_status == 200 and sse_headers.get("content-type", "").startswith(
        "text/event-stream"
    ), "authorized MCP GET did not establish an SSE stream"

    error = request(
        GATEWAY_URL,
        "/agents",
        body=rpc("tools/call", 6, {"name": "fetch", "arguments": {"url": ERROR_URL}}),
        headers=caller_headers(OWNER_KEY, "facade-backend-tool-error", session_id),
    )
    error_message = rpc_response(error, 6)
    assert error_message.get("result", {}).get("isError") is True, (
        "blank-url fetch did not return a genuine backend MCP tool error"
    )


def test_vikunja_route_render_is_byte_identical_with_facade_enabled_and_disabled() -> (
    None
):
    enabled = gateway_config(facade_enabled=True)
    disabled = gateway_config(facade_enabled=False)
    assert vikunja_route_bytes(enabled) == vikunja_route_bytes(disabled), (
        "rendered /vikunja route changed when the facade flag changed"
    )


def test_vikunja_optional_route_uses_the_real_sidecar_and_api(
    runtime: dict[str, Any],
) -> None:
    socket_path = runtime["socket_path"]
    optional_init = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc(
            "initialize",
            10,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "vikunja-same-request", "version": "1"},
            },
        ),
        headers=caller_headers(None, VIKUNJA_CALLER_MARKERS["optional_initialize"]),
    )
    assert optional_init[0] == 200 and optional_init[1].get("mcp-session-id"), (
        "/vikunja optional API-key baseline rejected a caller without X-Agent-Key"
    )
    optional_session_id = optional_init[1]["mcp-session-id"]

    optional_initialized = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc("notifications/initialized", None),
        headers=caller_headers(
            None,
            VIKUNJA_CALLER_MARKERS["optional_initialized"],
            optional_session_id,
        ),
    )
    assert optional_initialized[0] in (200, 202, 204), (
        "optional /vikunja MCP session did not initialize"
    )

    optional_listed = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc("tools/list", 11),
        headers=caller_headers(
            None,
            VIKUNJA_CALLER_MARKERS["optional_tools_list"],
            optional_session_id,
        ),
    )
    optional_tools = (
        rpc_response(optional_listed, 11).get("result", {}).get("tools", [])
    )
    assert any(tool.get("name") == "vikunja_tasks" for tool in optional_tools), (
        "/vikunja did not expose the role-rendered Vikunja MCP sidecar"
    )

    init = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc(
            "initialize",
            20,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "vikunja-same-request", "version": "1"},
            },
        ),
        headers=caller_headers(OWNER_KEY, VIKUNJA_CALLER_MARKERS["initialize"]),
    )
    assert init[0] == 200 and init[1].get("mcp-session-id"), (
        "caller-key /vikunja request could not initialize the role sidecar"
    )
    session_id = init[1]["mcp-session-id"]

    initialized = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc("notifications/initialized", None),
        headers=caller_headers(
            OWNER_KEY, VIKUNJA_CALLER_MARKERS["initialized"], session_id
        ),
    )
    assert initialized[0] in (200, 202, 204), (
        "keyed /vikunja MCP session did not initialize"
    )

    listed = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc("tools/list", 21),
        headers=caller_headers(
            OWNER_KEY, VIKUNJA_CALLER_MARKERS["tools_list"], session_id
        ),
    )
    tools = rpc_response(listed, 21).get("result", {}).get("tools", [])
    assert any(tool.get("name") == "vikunja_tasks" for tool in tools), (
        "keyed /vikunja route did not expose the role-rendered MCP sidecar"
    )

    before = stat(socket_path, "http.gateway_sidecar.downstream_rq_total")
    call = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc(
            "tools/call",
            22,
            {
                "name": "vikunja_tasks",
                "arguments": {"subcommand": "get", "id": runtime["vikunja_task_id"]},
            },
        ),
        headers=caller_headers(
            OWNER_KEY, VIKUNJA_CALLER_MARKERS["task_read"], session_id
        ),
    )
    result = rpc_response(call, 22).get("result", {})
    assert result.get("isError") is not True, (
        "same-request Vikunja task read returned an MCP error"
    )
    assert runtime["vikunja_task_title"] in json.dumps(result), (
        "same-request read did not return the known fixture task"
    )
    assert stat(socket_path, "http.gateway_sidecar.downstream_rq_total") > before, (
        "same-request Vikunja task read produced no gateway-to-sidecar observation"
    )
