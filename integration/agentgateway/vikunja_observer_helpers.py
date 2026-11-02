"""Envoy tap controls and assertions for the Vikunja sidecar hops."""

from __future__ import annotations

import http.client
import json
import time
from pathlib import Path
from typing import Any

from gateway_falsifier_contract import (
    CALLER_CREDENTIAL_HEADERS,
    PROTOCOL_VERSION,
    SIDECAR_OBSERVER_URL,
)
from gateway_observer_helpers import (
    admin,
    body_json,
    load_traces,
    stat,
    trace_files,
    trace_headers,
    wait_for_capture,
)
from gateway_protocol_helpers import caller_headers, request, rpc, rpc_response


def sidecar_listener_positive_control(runtime: dict[str, Any]) -> None:
    socket_path = runtime["socket_path"]
    trace_dir = runtime["gateway_sidecar_trace_dir"]
    marker = "gateway-sidecar-positive-control"
    marker_headers = {
        "Authorization": f"Bearer control-{marker}",
        "Proxy-Authorization": f"Basic control-{marker}",
        "Cookie": f"session=control-{marker}",
        "X-Agent-Key": "fixture-control-key-not-a-secret",
        "X-Falsifier-Marker": marker,
    }
    deadline = time.monotonic() + 45
    init: tuple[int, dict[str, str], bytes] | None = None
    while time.monotonic() < deadline:
        try:
            candidate = request(
                SIDECAR_OBSERVER_URL,
                "/mcp",
                body=rpc(
                    "initialize",
                    200,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "gateway-sidecar-control",
                            "version": "1",
                        },
                    },
                ),
                headers=marker_headers,
            )
            if candidate[0] == 200 and candidate[1].get("mcp-session-id"):
                init = candidate
                break
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    assert init is not None, (
        "gateway-to-sidecar tap positive control could not initialize MCP"
    )
    session_id = init[1]["mcp-session-id"]
    session_headers = {
        **marker_headers,
        "Mcp-Session-Id": session_id,
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    }
    initialized = request(
        SIDECAR_OBSERVER_URL,
        "/mcp",
        body=rpc("notifications/initialized", None),
        headers=session_headers,
    )
    assert initialized[0] in (200, 202, 204), (
        "gateway-to-sidecar tap positive control did not initialize its MCP session"
    )
    response = request(
        SIDECAR_OBSERVER_URL,
        "/mcp",
        body=rpc("tools/list", 201),
        headers=session_headers,
    )
    listing = rpc_response(response, 201)
    assert any(
        tool["name"] == "vikunja_tasks" for tool in listing["result"]["tools"]
    ), "gateway-to-sidecar tap positive control did not reach the role sidecar"

    total = wait_for_capture(
        socket_path,
        trace_dir,
        minimum=3,
        stat_prefix="gateway_sidecar",
    )
    traces = load_traces(trace_dir)
    captured = [
        trace
        for trace in traces
        if (body_json(trace["request"]) or {}).get("method") == "tools/list"
    ]
    assert len(traces) == total and captured, (
        "gateway-to-sidecar tap positive control did not reconcile with Envoy counters"
    )
    assert any(
        all(
            trace_headers(trace).get(name.lower()) == [value]
            for name, value in marker_headers.items()
        )
        for trace in captured
    ), "gateway-to-sidecar tap missed its synthetic header positive control"
    clear_capture(trace_dir)
    status, _ = admin(socket_path, "POST", "/reset_counters")
    assert (
        status == 200
        and stat(socket_path, "http.gateway_sidecar.downstream_rq_total") == 0
    ), "gateway-to-sidecar positive-control counters were not reset"


def sidecar_api_positive_control(runtime: dict[str, Any]) -> None:
    socket_path = runtime["socket_path"]
    trace_dir = runtime["sidecar_api_trace_dir"]
    sidecar_url = runtime["sidecar_url"]
    task_id = runtime["vikunja_task_id"]
    read_token = runtime["vikunja_read_token"]
    marker = "sidecar-api-positive-control"

    init = request(
        sidecar_url,
        "/mcp",
        body=rpc(
            "initialize",
            201,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "sidecar-api-control", "version": "1"},
            },
        ),
    )
    assert init[0] == 200 and init[1].get("mcp-session-id"), (
        "direct sidecar positive control could not initialize MCP"
    )
    session_id = init[1]["mcp-session-id"]
    initialized = request(
        sidecar_url,
        "/mcp",
        body=rpc("notifications/initialized", None),
        headers={
            "Mcp-Session-Id": session_id,
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        },
    )
    assert initialized[0] in (200, 202, 204), "direct sidecar MCP initialization failed"
    call = request(
        sidecar_url,
        "/mcp",
        body=rpc(
            "tools/call",
            202,
            {
                "name": "vikunja_tasks",
                "arguments": {"subcommand": "get", "id": task_id},
            },
        ),
        headers=caller_headers(None, marker, session_id),
    )
    result = rpc_response(call, 202).get("result", {})
    assert marker not in json.dumps(result), (
        "sidecar positive control echoed a caller marker"
    )

    total = wait_for_capture(
        socket_path,
        trace_dir,
        minimum=1,
        stat_prefix="sidecar_api",
    )
    traces = load_traces(trace_dir)
    backend_auth_matches = sidecar_api_auth_matches(traces, read_token)
    assert len(traces) == total and backend_auth_matches, (
        "sidecar-to-Vikunja API tap positive control missed backend authorization"
    )
    assert sidecar_api_has_no_caller_material(traces, {marker}), (
        "sidecar-to-Vikunja API tap positive control observed caller material"
    )
    assert sidecar_api_read_matches(traces, task_id), (
        "sidecar-to-Vikunja API tap positive control did not read the known fixture task"
    )
    clear_capture(trace_dir)
    status, _ = admin(socket_path, "POST", "/reset_counters")
    assert (
        status == 200 and stat(socket_path, "http.sidecar_api.downstream_rq_total") == 0
    ), "sidecar-to-Vikunja API positive-control counters were not reset"


def clear_capture(trace_dir: Path) -> None:
    for path in trace_files(trace_dir):
        path.unlink()


def gateway_sidecar_credentials_stripped(traces: list[dict[str, Any]]) -> bool:
    return bool(traces) and all(
        CALLER_CREDENTIAL_HEADERS.isdisjoint(trace_headers(trace)) for trace in traces
    )


def sidecar_api_auth_matches(traces: list[dict[str, Any]], read_token: str) -> bool:
    expected = f"Bearer {read_token}"
    return bool(traces) and all(
        trace_headers(trace).get("authorization") == [expected] for trace in traces
    )


def sidecar_api_has_no_caller_material(
    traces: list[dict[str, Any]], markers: set[str]
) -> bool:
    if not traces:
        return False
    caller_headers = CALLER_CREDENTIAL_HEADERS | {
        "mcp-session-id",
        "mcp-protocol-version",
        "x-falsifier-marker",
    }
    caller_headers.discard("authorization")
    for trace in traces:
        headers = trace_headers(trace)
        if caller_headers.intersection(headers):
            return False
        values = [value for items in headers.values() for value in items]
        request_body = trace["request"].get("body", {}).get("asString", "")
        if any(marker in value for marker in markers for value in values):
            return False
        if any(marker in request_body for marker in markers):
            return False
    return True


def sidecar_api_read_matches(traces: list[dict[str, Any]], task_id: int) -> bool:
    for trace in traces:
        headers = trace_headers(trace)
        path = headers.get(":path", [""])[0].split("?", 1)[0]
        if headers.get(":method") != ["GET"] or path != f"/api/v1/tasks/{task_id}":
            continue
        body = body_json(trace["response"])
        if isinstance(body, dict) and body.get("id") == task_id:
            return True
    return False


def vikunja_request_matches(
    gateway_traces: list[dict[str, Any]],
    api_traces: list[dict[str, Any]],
    task_id: int,
    task_title: str,
) -> bool:
    gateway_call_seen = False
    for trace in gateway_traces:
        request_message = body_json(trace["request"])
        if not request_message or request_message.get("method") != "tools/call":
            continue
        params = request_message.get("params", {})
        if (
            params.get("name") == "vikunja_tasks"
            and params.get("arguments", {}).get("subcommand") == "get"
            and params.get("arguments", {}).get("id") == task_id
        ):
            result = body_json(trace["response"])
            if result and task_title in json.dumps(result):
                gateway_call_seen = True
                break
    if not gateway_call_seen:
        return False
    for trace in api_traces:
        headers = trace_headers(trace)
        path = headers.get(":path", [""])[0].split("?", 1)[0]
        if headers.get(":method") != ["GET"] or path != f"/api/v1/tasks/{task_id}":
            continue
        body = body_json(trace["response"])
        if (
            isinstance(body, dict)
            and body.get("id") == task_id
            and body.get("title") == task_title
        ):
            return True
    return False


def wait_for_sidecar(base_url: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            response = request(
                base_url,
                "/mcp",
                body=rpc(
                    "initialize",
                    199,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "sidecar-readiness", "version": "1"},
                    },
                ),
            )
            if response[0] == 200 and response[1].get("mcp-session-id"):
                session_id = response[1]["mcp-session-id"]
                initialized = request(
                    base_url,
                    "/mcp",
                    body=rpc("notifications/initialized", None),
                    headers={
                        "Mcp-Session-Id": session_id,
                        "MCP-Protocol-Version": PROTOCOL_VERSION,
                    },
                )
                if initialized[0] not in (200, 202, 204):
                    continue
                listed = request(
                    base_url,
                    "/mcp",
                    body=rpc("tools/list", 200),
                    headers={
                        "Mcp-Session-Id": session_id,
                        "MCP-Protocol-Version": PROTOCOL_VERSION,
                    },
                )
                if listed[0] == 200:
                    message = rpc_response(listed, 200)
                    if any(
                        tool.get("name") == "vikunja_tasks"
                        for tool in message.get("result", {}).get("tools", [])
                    ):
                        return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    raise AssertionError("role-rendered Vikunja sidecar did not expose its MCP tool")
