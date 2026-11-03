"""Dify route code and the shared request and logging helpers for hermes-event-relay.

Imported by hermes-event-relay.py, which is deployed next to this file.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def checked_url(url: str) -> str:
    """Allow https anywhere and http only to the loopback receiver."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK):
        return url
    raise ValueError(f"refusing URL scheme/host: {parts.scheme}://{parts.hostname}")


def send_request(req: urllib.request.Request, timeout: float) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(200).decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode(errors="replace")
    except OSError as exc:
        return None, type(exc).__name__


def log(**fields) -> None:
    print(json.dumps({"ts": int(time.time()), **fields}, sort_keys=True), flush=True)


def _route_problem(route) -> str | None:
    """Name what makes a route unusable at start, or return None when it is usable."""
    if not isinstance(route, dict):
        return "route entry is not a mapping"
    for field in ("name", "base", "app_key_env"):
        if not isinstance(route.get(field), str) or not route.get(field):
            return f"{field} must be a non-empty string"
    match = route.get("match")
    if not isinstance(match, dict) or not isinstance(match.get("event"), str):
        return "match.event must be a string"
    if not isinstance(match.get("action", ""), (str, list)):
        return "match.action must be a string or a list"
    if not isinstance(match.get("where", {}), dict):
        return "match.where must be a mapping"
    if not isinstance(route.get("inputs", {}), dict):
        return "inputs must be a mapping"
    text_inputs = route.get("text_inputs", [])
    if not isinstance(text_inputs, list) or not all(isinstance(name, str) for name in text_inputs):
        return "text_inputs must be a list of input names"
    const = route.get("const", {})
    if not isinstance(const, dict) or not all(isinstance(v, str) for v in const.values()):
        return "const must map input names to strings"
    required = route.get("required", [])
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        return "required must be a list of input names"
    timeout = route.get("timeout", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return "timeout must be a number"
    return None


def load_routes(raw_json: str, env: dict) -> list[dict]:
    """Parse RELAY_ROUTES_JSON into active routes. A route whose app-key env var is
    unset is dropped and logged, so the relay keeps running on the default route."""
    if not raw_json:
        return []
    try:
        routes = json.loads(raw_json)
        if not isinstance(routes, list):
            raise ValueError("not a list")
    except ValueError:
        log(msg="route_config_invalid", error="RELAY_ROUTES_JSON is not a JSON list")
        return []
    active = []
    for route in routes:
        problem = _route_problem(route)
        if problem:
            log(msg="route_disabled", route=route.get("name") if isinstance(route, dict) else None, reason=problem)
            continue
        key = env.get(route["app_key_env"], "")
        if not key:
            log(msg="route_disabled", route=route["name"], reason="app key env is unset")
            continue
        active.append({**route, "app_key": key})
    return active


def _path(payload, dotted: str):
    cur = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def match_routes(event: str, raw: bytes, routes: list[dict]) -> list[dict]:
    """Return every active route whose event, action and `where` values match, in config order."""
    if not routes:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    matched = []
    for route in routes:
        match = route["match"]
        if match.get("event") != event:
            continue
        actions = match.get("action")
        if actions is not None and _path(payload, "action") not in ([actions] if isinstance(actions, str) else actions):
            continue
        if all(_path(payload, key) == value for key, value in match.get("where", {}).items()):
            matched.append(route)
    return matched


def dify_inputs(event: str, raw: bytes, mapping: dict, text_inputs=(), const=None) -> dict:
    """Map payload fields (dotted paths) to a Dify workflow's inputs. Missing fields become ''.

    Booleans become "true" or "false" only for the names in text_inputs, which the route
    declares as Dify text inputs. Other booleans pass through unchanged. Constants from the
    route (const) are added as fixed inputs, and event is always set from the envelope.
    """
    payload = json.loads(raw)
    inputs = {"event": event, **(const or {})}
    for name, dotted in mapping.items():
        value = _path(payload, dotted)
        if name in text_inputs and isinstance(value, bool):
            value = "true" if value else "false"
        inputs[name] = "" if value is None else value
    return inputs


def forward_dify(base: str, app_key: str, inputs: dict, timeout: float) -> tuple[int | None, str | None, str]:
    """Start one streaming run of a Dify workflow app and return (status, workflow_run_id, detail).

    Reads only until the workflow_started event, then closes the stream. Dify 1.18
    runs the workflow in its own worker thread, and a closed listener does not stop
    it; the only abort is its execution-time watchdog. The app key goes only in the header.
    """
    body = json.dumps({"inputs": inputs, "response_mode": "streaming", "user": "github-event-relay"}).encode()
    req = urllib.request.Request(checked_url(f"{base.rstrip('/')}/v1/workflows/run"), data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {app_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                text = line.decode(errors="replace").strip()
                if not text.startswith("data:"):
                    continue
                try:
                    event = json.loads(text[len("data:"):])
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") == "workflow_started":
                    return resp.status, str(event.get("workflow_run_id") or ""), ""
                if event.get("event") == "error":
                    return resp.status, None, str(event.get("message") or "error")[:200]
            return resp.status, None, "stream ended before workflow_started"
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read(200).decode(errors="replace")
    except (http.client.HTTPException, OSError) as exc:
        return None, None, type(exc).__name__
