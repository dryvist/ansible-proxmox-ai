"""Exercise the pinned gateway image with the role-rendered Vikunja routes."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest
import yaml

from test_authz_render import ROLE_DEFAULTS, _render, _route_for_path, _routes

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = Path(__file__).with_name("compose.yml")
TESTBOX_SOURCE_COMMIT = "ea5608642b9d5c94c5baf5fd9f2f9849b808e963"
TESTBOX_IMAGE = "gateway-falsifier/testbox:0.0.1"
ENVOY_IMAGE = (
    "envoyproxy/envoy:v1.40.1@sha256:"
    "713bb146f1a7c97ebc70e13d259f77ff36f2c5d00f3145677816af5af9bd1530"
)
GATEWAY_URL = "http://127.0.0.1:15000"
OBSERVER_URL = "http://127.0.0.1:18002"
OWNER = "falsifier-owner"
NONOWNER = "falsifier-nonowner"
OWNER_KEY = "fixture-owner-key-not-a-secret"
NONOWNER_KEY = "fixture-nonowner-key-not-a-secret"
BAD_KEY = "fixture-invalid-key-not-a-secret"
TOY_BACKEND_AUTH = "fixture-toy-backend-auth-not-a-secret"
PROTOCOL_VERSION = "2025-03-26"
CALLER_CREDENTIAL_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-agent-key",
}
SUCCESS_URL = "https://fixture.invalid/falsifier-success"
DIRECT_SUCCESS_URL = "https://fixture.invalid/direct-success"
ERROR_URL = ""


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path) -> None:
        super().__init__("localhost", timeout=10)
        self.socket_path = str(path)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def request(
    base_url: str,
    path: str,
    *,
    method: str = "POST",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    close_after_headers: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    url = urlsplit(base_url)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=10)
    request_headers = {"Accept": "application/json, text/event-stream", **(headers or {})}
    payload = json.dumps(body).encode() if body is not None else None
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=payload, headers=request_headers)
    response = connection.getresponse()
    response_headers = {name.lower(): value for name, value in response.getheaders()}
    if close_after_headers:
        connection.close()
        return response.status, response_headers, b""
    result = response.read()
    connection.close()
    return response.status, response_headers, result


def admin(socket_path: Path, method: str, path: str) -> tuple[int, bytes]:
    connection = UnixHTTPConnection(socket_path)
    connection.request(method, path)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def rpc(method: str, request_id: int | None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        result["id"] = request_id
    if params is not None:
        result["params"] = params
    return result


def caller_headers(key: str | None, marker: str, session_id: str | None = None) -> dict[str, str]:
    result = {
        "Authorization": f"Bearer caller-{marker}",
        "Proxy-Authorization": f"Basic caller-{marker}",
        "Cookie": f"session=caller-{marker}",
        "X-Falsifier-Marker": marker,
    }
    if key is not None:
        result["X-Agent-Key"] = key
    if session_id is not None:
        result["Mcp-Session-Id"] = session_id
        result["MCP-Protocol-Version"] = PROTOCOL_VERSION
    return result


def messages(content_type: str, body: bytes) -> list[dict[str, Any]]:
    if "text/event-stream" in content_type:
        return [
            json.loads(line[5:].strip())
            for line in body.decode().splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
    assert "application/json" in content_type, "MCP response has an unexpected content type"
    value = json.loads(body)
    return value if isinstance(value, list) else [value]


def rpc_response(
    response: tuple[int, dict[str, str], bytes], request_id: int
) -> dict[str, Any]:
    status, headers, body = response
    assert status == 200, "authorized MCP request did not succeed"
    message = next(
        (item for item in messages(headers.get("content-type", ""), body) if item.get("id") == request_id),
        None,
    )
    assert message is not None, "MCP response did not correlate to the frontend request id"
    return message


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


@pytest.fixture(scope="module")
def runtime() -> Any:
    image = str(ROLE_DEFAULTS["agentgateway_docker_image"])
    assert re.fullmatch(r".+:v1\.6\.0@sha256:[0-9a-f]{64}", image), (
        "the falsifier requires the digest-pinned agentgateway v1.6.0 role image"
    )
    source_dir = Path(os.environ.get("AGENTGATEWAY_SOURCE_DIR", REPO_ROOT / ".agentgateway"))
    source_commit = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert source_commit == TESTBOX_SOURCE_COMMIT, "testbox source is not the reviewed upstream commit"

    root = Path(os.environ.get("FALSIFIER_RUNTIME_DIR", tempfile.gettempdir()))
    root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="agentgateway-falsifier-", dir=root))
    socket_dir, trace_dir = run_dir / "envoy", run_dir / "tap"
    socket_dir.mkdir(mode=0o777)
    trace_dir.mkdir(mode=0o777)
    socket_dir.chmod(0o777)
    trace_dir.chmod(0o777)
    config_path = run_dir / "config.yaml"
    rendered = gateway_config()
    config_path.write_text(rendered)

    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT")
        if key in os.environ
    }
    env["DOCKER_CONFIG"] = str(run_dir / "empty-docker-config")
    Path(env["DOCKER_CONFIG"]).mkdir()
    env.update(
        {
            "AGENTGATEWAY_IMAGE": image,
            "GATEWAY_CONFIG": str(config_path),
            "ENVOY_RUNTIME_DIR": str(socket_dir),
            "TAP_OUTPUT_DIR": str(trace_dir),
        }
    )
    compose = [
        "docker",
        "compose",
        "--project-name",
        f"gateway-falsifier-{os.getpid()}",
        "--file",
        str(COMPOSE_FILE),
    ]

    def compose_run(*args: str) -> None:
        subprocess.run(
            [*compose, *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    context: dict[str, Any] = {
        "socket_path": socket_dir / "admin.sock",
        "trace_dir": trace_dir,
    }
    stack_started = False
    test_started = False
    try:
        stack_started = True
        compose_run("up", "--detach", "--no-build", "observer", "testbox")
        wait_for_admin(context["socket_path"])
        observer_positive_control(context)

        compose_run("up", "--detach", "--no-build", "agentgateway")
        wait_for_gateway()
        context["rendered_config_sha256"] = hashlib.sha256(rendered.encode()).hexdigest()
        context["source_commit"] = source_commit
        context["images"] = {
            "agentgateway": image_identity(env, image),
            "envoy": image_identity(env, ENVOY_IMAGE),
            "testbox": image_identity(env, TESTBOX_IMAGE),
        }
        test_started = True
        yield context
    finally:
        try:
            if stack_started:
                try:
                    if test_started:
                        compose_run("stop", "--timeout", "3", "agentgateway")
                        traces_count = wait_for_capture(context["socket_path"], trace_dir)
                        traces = load_traces(trace_dir)
                        assert traces_count > 0 and len(traces) == traces_count, (
                            "no upstream MCP exchanges were captured or tap traces are incomplete"
                        )
                        for trace in traces:
                            assert CALLER_CREDENTIAL_HEADERS.isdisjoint(trace_headers(trace)), (
                                "a caller credential reached the Vikunja MCP backend"
                            )
                        assert_backend_calls(traces)
                        receipt = {
                            "acceptance_status": "pytest exit status is authoritative",
                            "agentgateway": context["images"]["agentgateway"],
                            "envoy": context["images"]["envoy"],
                            "testbox": context["images"]["testbox"],
                            "testbox_source_commit": source_commit,
                            "host_platform": f"{platform.system().lower()}/{platform.machine().lower()}",
                            "rendered_config_sha256": context["rendered_config_sha256"],
                            "client_protocol_version": PROTOCOL_VERSION,
                            "observed_http_request_count": traces_count,
                        }
                        print(f"gateway_falsifier_identity_only={json.dumps(receipt, sort_keys=True)}")
                finally:
                    compose_run("down", "--volumes", "--remove-orphans")
        finally:
            shutil.rmtree(run_dir)


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
