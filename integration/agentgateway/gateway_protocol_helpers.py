"""Native HTTP and JSON-RPC helpers used by the gateway fixture cases."""

from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import urlsplit

from gateway_falsifier_contract import PROTOCOL_VERSION


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
