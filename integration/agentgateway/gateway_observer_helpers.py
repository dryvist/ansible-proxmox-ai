"""Role rendering and Envoy observation helpers for the gateway fixture."""

from __future__ import annotations

import http.client
import json
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml
from gateway_falsifier_contract import (
    DIRECT_SUCCESS_URL,
    ERROR_URL,
    GATEWAY_URL,
    NONOWNER,
    NONOWNER_KEY,
    OBSERVER_URL,
    OWNER,
    OWNER_KEY,
    PROTOCOL_VERSION,
    SUCCESS_URL,
    TOY_BACKEND_AUTH,
)
from gateway_protocol_helpers import request, rpc, rpc_response
from test_authz_render import ROLE_DEFAULTS, _render, _route_for_path, _routes


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path) -> None:
        super().__init__("localhost", timeout=10)
        self.socket_path = str(path)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def admin(socket_path: Path, method: str, path: str) -> tuple[int, bytes]:
    connection = UnixHTTPConnection(socket_path)
    connection.request(method, path)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def trace_files(trace_dir: Path) -> list[Path]:
    return sorted(trace_dir.glob("trace_*.json"))


def body_messages(message: dict[str, Any]) -> list[dict[str, Any]]:
    body = message.get("body", {})
    text = body.get("asString", body.get("as_string", ""))
    if not text:
        return []
    if "text/event-stream" in next(
        (item.get("value", "") for item in message.get("headers", []) if item.get("key", "").lower() == "content-type"),
        "",
    ):
        return [
            json.loads(line[5:].strip())
            for line in text.splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
    value = json.loads(text)
    return value if isinstance(value, list) else [value]


def body_json(message: dict[str, Any]) -> dict[str, Any] | None:
    parsed = body_messages(message)
    return parsed[0] if len(parsed) == 1 else None


def load_traces(trace_dir: Path) -> list[dict[str, Any]]:
    traces = []
    for path in trace_files(trace_dir):
        wrapper = json.loads(path.read_text())
        trace = wrapper.get("httpBufferedTrace", wrapper.get("http_buffered_trace"))
        assert isinstance(trace, dict), "tap file does not contain an Envoy HttpBufferedTrace"
        req, resp = trace.get("request"), trace.get("response")
        assert isinstance(req, dict) and isinstance(resp, dict), "tap trace is incomplete"
        assert isinstance(req.get("headers"), list) and isinstance(resp.get("headers"), list), (
            "tap trace omitted request or response headers"
        )
        assert not req.get("body", {}).get("truncated", False), "tap truncated an MCP request body"
        assert not resp.get("body", {}).get("truncated", False), "tap truncated an MCP response body"
        traces.append(trace)
    return traces


def trace_headers(trace: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in trace["request"]["headers"]:
        name = item.get("key", "").lower()
        assert name, "tap returned a header without a name"
        result.setdefault(name, []).append(item.get("value", ""))
    return result


def stat(socket_path: Path, name: str) -> int:
    query = urlencode({"format": "json", "filter": f"^{re.escape(name)}$"})
    status, body = admin(socket_path, "GET", f"/stats?{query}")
    assert status == 200, "Envoy stats endpoint failed"
    decoded = json.loads(body)
    stats = decoded.get("stats", decoded) if isinstance(decoded, dict) else decoded
    values = [int(item["value"]) for item in stats if item.get("name") == name]
    assert len(values) == 1, f"Envoy did not report exactly one {name} stat"
    return values[0]


def wait_for_capture(socket_path: Path, trace_dir: Path, minimum: int = 0) -> int:
    total_name = "http.mcp_observer.downstream_rq_total"
    active_name = "http.mcp_observer.downstream_rq_active"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        total = stat(socket_path, total_name)
        active = stat(socket_path, active_name)
        if total >= minimum and active == 0 and len(trace_files(trace_dir)) == total:
            return total
        time.sleep(0.1)
    raise AssertionError("Envoy tap files did not reconcile with completed upstream requests")


def gateway_config() -> str:
    roster = {
        OWNER: {"owns": ["vikunja"], "trust_tier": "trusted"},
        NONOWNER: {"owns": [], "trust_tier": "trusted"},
    }
    keys = {
        "falsifier_owner_agentgateway_key": OWNER_KEY,
        "falsifier_nonowner_agentgateway_key": NONOWNER_KEY,
    }
    rendered = _render(
        roster,
        {"vikunja": OWNER},
        keys,
        extra={
            "agentgateway_docker_facade_enabled": True,
            "agentgateway_docker_facade_path": "/agents",
            "agentgateway_docker_facade_callers": [OWNER],
            "agentgateway_docker_proxy_port": 15000,
            "agentgateway_docker_admin_port": 15001,
            "agentgateway_docker_metrics_port": 15002,
            "agentgateway_docker_otlp_endpoint": "http://observer:4318",
            "agentgateway_docker_trace_sampling": "false",
            "agentgateway_docker_vikunja_mcp_port": 8002,
            "agentgateway_docker_vikunja_read_token": "fixture-sidecar-token-not-a-secret",
            "agentgateway_docker_mcp_targets": [],
            "agentgateway_docker_qdrant_mcp_enabled": False,
            "agentgateway_docker_docs_mcp_enabled": False,
            "agentgateway_docker_nautobot_mcp_enabled": False,
            "agentgateway_docker_nautobot_openapi_enabled": False,
            "agentgateway_docker_hindsight_enabled": False,
            "agentgateway_docker_a2a_targets": [],
        },
    )
    config = yaml.safe_load(rendered)
    for path in ("/agents", "/vikunja"):
        policies = _route_for_path(_routes(rendered), path)["policies"]
        assert policies["apiKey"]["mode"] == "strict", f"{path} must require a caller key"
        assert policies["authorization"]["rules"] == [{"allow": f"apiKey.agent == '{OWNER}'"}], (
            f"{path} must authorize only the configured caller"
        )
        agents = {item["metadata"]["agent"] for item in policies["apiKey"]["keys"]}
        assert {OWNER, NONOWNER} <= agents, f"{path} lacks owner or non-owner roster key"
        removed = {name.lower() for name in policies["requestHeaderModifier"]["remove"]}
        required = set(ROLE_DEFAULTS["agentgateway_docker_caller_credential_headers"])
        assert required <= removed, f"{path} omits a configured caller credential header"
    assert config["binds"][0]["listeners"][0]["routes"]
    return rendered


def image_identity(env: dict[str, str], image: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format={{.Id}}/{{.Os}}/{{.Architecture}}", image],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    image_id, image_os, architecture = result.stdout.strip().split("/", 2)
    assert image_id.startswith("sha256:"), "Docker did not report an immutable image ID"
    return {"ref": image, "id": image_id, "platform": f"{image_os}/{architecture}"}


def observer_positive_control(runtime: dict[str, Any]) -> None:
    deadline = time.monotonic() + 45
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            init = request(
                OBSERVER_URL,
                "/mcp",
                body=rpc(
                    "initialize",
                    100,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "observer-control", "version": "1"},
                    },
                ),
            )
            if init[0] == 200 and init[1].get("mcp-session-id"):
                break
            last_error = f"HTTP {init[0]} without a usable MCP session"
        except (OSError, http.client.HTTPException) as error:
            last_error = str(error)
        time.sleep(0.25)
    else:
        raise AssertionError(f"observer MCP listener did not become ready: {last_error}")
    initialized = request(
        OBSERVER_URL,
        "/mcp",
        body=rpc("notifications/initialized", None),
        headers={
            "Mcp-Session-Id": init[1]["mcp-session-id"],
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        },
    )
    assert initialized[0] in (200, 202, 204), "direct observer session initialization failed"
    call = request(
        OBSERVER_URL,
        "/mcp",
        body=rpc("tools/call", 101, {"name": "fetch", "arguments": {"url": SUCCESS_URL}}),
        headers={
            "Mcp-Session-Id": init[1]["mcp-session-id"],
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Authorization": f"Bearer {TOY_BACKEND_AUTH}",
        },
    )
    result = rpc_response(call, 101).get("result", {})
    assert result.get("isError") is not True, "direct observer tool call failed"

    socket_path, trace_dir = runtime["socket_path"], runtime["trace_dir"]
    total = wait_for_capture(socket_path, trace_dir, minimum=1)
    traces = load_traces(trace_dir)
    positive = [
        trace
        for trace in traces
        if (body_json(trace["request"]) or {}).get("method") == "tools/call"
        and SUCCESS_URL in json.dumps(body_json(trace["request"]))
    ]
    assert positive, "direct observer tool call was not captured"
    assert any(
        trace_headers(trace).get("authorization") == [f"Bearer {TOY_BACKEND_AUTH}"]
        for trace in positive
    ), "tap positive control missed the synthetic backend authorization header"
    assert len(traces) == total, "positive-control traces did not match Envoy's upstream counter"

    for path in trace_files(trace_dir):
        path.unlink()
    status, _ = admin(socket_path, "POST", "/reset_counters")
    assert status == 200 and stat(socket_path, "http.mcp_observer.downstream_rq_total") == 0, (
        "positive-control upstream counters were not reset"
    )


def wait_for_admin(socket_path: Path) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            if admin(socket_path, "GET", "/server_info")[0] == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    raise AssertionError("Envoy admin socket did not become ready")


def wait_for_gateway() -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            status, _, _ = request(
                GATEWAY_URL,
                "/agents",
                body=rpc(
                    "initialize",
                    90,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "readiness", "version": "1"},
                    },
                ),
            )
            if status in (401, 403):
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    raise AssertionError("gateway did not become ready with the facade route active")


def assert_backend_calls(traces: list[dict[str, Any]]) -> None:
    calls: dict[str, list[dict[str, Any]]] = {SUCCESS_URL: [], DIRECT_SUCCESS_URL: [], ERROR_URL: []}
    for trace in traces:
        message = body_json(trace["request"])
        if not message or message.get("method") != "tools/call":
            continue
        url = message.get("params", {}).get("arguments", {}).get("url")
        if url in calls:
            calls[url].append(trace)
    assert all(calls.values()), "one or more frontend tool calls were not observed at the backend"

    assert any(
        any(
            content.get("text") == f"fetched: {SUCCESS_URL}"
            for content in (body_json(trace["response"]) or {}).get("result", {}).get("content", [])
        )
        for trace in calls[SUCCESS_URL]
    ), "backend trace did not contain the successful fixture fetch result"
    assert any(
        any(
            content.get("text") == f"fetched: {DIRECT_SUCCESS_URL}"
            for content in (body_json(trace["response"]) or {}).get("result", {}).get("content", [])
        )
        for trace in calls[DIRECT_SUCCESS_URL]
    ), "backend trace did not contain the successful direct-route fetch result"
    assert any(
        (body_json(trace["response"]) or {}).get("result", {}).get("isError") is True
        for trace in calls[ERROR_URL]
    ), "backend trace did not contain the genuine MCP tool error"
