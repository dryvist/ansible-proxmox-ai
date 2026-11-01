"""Behavior assertions for the role-rendered gateway's native MCP routes."""

from __future__ import annotations

from typing import Any

from gateway_falsifier_contract import (
    BAD_KEY,
    DIRECT_SUCCESS_URL,
    ERROR_URL,
    GATEWAY_URL,
    NONOWNER_KEY,
    OWNER_KEY,
    PROTOCOL_VERSION,
    SUCCESS_URL,
)
from gateway_observer_helpers import stat
from gateway_protocol_helpers import caller_headers, request, rpc, rpc_response


def test_gateway_fixture_auth_and_mcp_lifecycle(runtime: dict[str, Any]) -> None:
    socket_path = runtime["socket_path"]
    for path in ("/agents", "/vikunja"):
        for key in (None, BAD_KEY, NONOWNER_KEY):
            marker = f"deny-{path.strip('/')}-{key or 'missing'}"
            before = stat(socket_path, "http.mcp_observer.downstream_rq_total")
            response = request(
                GATEWAY_URL,
                path,
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
            assert response[0] in (401, 403), f"{path} did not reject {marker}"
            assert after == before, f"{path} forwarded denied request {marker} upstream"

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
    assert init[0] == 200 and init[1].get("mcp-session-id"), "authorized facade initialize failed"
    session_id = init[1]["mcp-session-id"]

    before = stat(socket_path, "http.mcp_observer.downstream_rq_total")
    denied = request(
        GATEWAY_URL,
        "/agents",
        body=rpc("tools/list", 3),
        headers=caller_headers(None, "facade-continuation-missing-key", session_id),
    )
    assert denied[0] in (401, 403), "gateway accepted a continuation without its caller key"
    assert stat(socket_path, "http.mcp_observer.downstream_rq_total") == before, (
        "keyless MCP continuation reached the backend"
    )

    initialized = request(
        GATEWAY_URL,
        "/agents",
        body=rpc("notifications/initialized", None),
        headers=caller_headers(OWNER_KEY, "facade-initialized", session_id),
    )
    assert initialized[0] in (200, 202, 204), "authorized initialized notification failed"

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
    assert any(content.get("text") == f"fetched: {SUCCESS_URL}" for content in result.get("content", [])), (
        "successful fetch did not return the exact fixture result"
    )
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
    assert sse_status == 200 and sse_headers.get("content-type", "").startswith("text/event-stream"), (
        "authorized MCP GET did not establish an SSE stream"
    )

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

    direct = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc(
            "initialize",
            7,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "direct-route-probe", "version": "1"},
            },
        ),
        headers=caller_headers(OWNER_KEY, "direct-vikunja-initialize"),
    )
    assert direct[0] == 200 and direct[1].get("mcp-session-id"), (
        "authorized direct Vikunja initialize failed"
    )
    direct_session_id = direct[1]["mcp-session-id"]
    initialized = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc("notifications/initialized", None),
        headers=caller_headers(OWNER_KEY, "direct-initialized", direct_session_id),
    )
    assert initialized[0] in (200, 202, 204), "direct route session initialization failed"
    direct_call = request(
        GATEWAY_URL,
        "/vikunja",
        body=rpc("tools/call", 8, {"name": "fetch", "arguments": {"url": DIRECT_SUCCESS_URL}}),
        headers=caller_headers(OWNER_KEY, "direct-tools-call", direct_session_id),
    )
    result = rpc_response(direct_call, 8).get("result", {})
    assert any(content.get("text") == f"fetched: {DIRECT_SUCCESS_URL}" for content in result.get("content", [])), (
        "direct-route fetch did not return the exact backend result"
    )
