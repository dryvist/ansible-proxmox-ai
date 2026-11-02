"""Native API, role-render, Compose, and MCP Inspector helpers for the fixture."""

from __future__ import annotations

import json
import secrets
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
from test_authz_render import ENV, ROLE_DEFAULTS
from test_sidecar_dependencies import COMPOSE_TEMPLATE, EXAMPLE_VALUES

COMPOSE_BASE = Path(__file__).with_name("compose.yml")
VIKUNJA_IMAGE = (
    "vikunja/vikunja:2.7.1@sha256:"
    "f7dc8428d10aef17f3075886d3c1c022f566ec456f88985f02c137f4fc534576"
)
INSPECTOR_PACKAGE = "@modelcontextprotocol/inspector@2.12.0"
FIXTURE_USER_PREFIX = "vikunja-ro-falsifier-"
FIXTURE_TASK_TITLE = "Scoped token proof fixture"
_FIXTURE_SECRETS: list[str] = []
API_TIMEOUT_SECONDS = 15
COMMAND_TIMEOUT_SECONDS = 90


def _run(
    command: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        env=env,
    )
    # Compose may surface container diagnostics. Keep them out of pytest
    # failures because they can include environment values from this fixture.
    assert result.returncode == 0, (
        f"command failed: {command[0]} (exit {result.returncode})"
    )
    return result


def _api(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(f"{base_url}{path}", data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            data = response.read()
            status = response.status
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
    except HTTPError as response:
        data = response.read()
        status = response.code
        response_headers = {
            key.lower(): value for key, value in response.headers.items()
        }
    parsed = json.loads(data) if data else {}
    assert isinstance(parsed, dict), (
        f"Vikunja returned a non-object response for {method} {path}"
    )
    return status, parsed, response_headers


def _render_sidecar(
    *,
    token: str,
    port: int,
    api_url: str = "http://vikunja:3456/api/v1",
    network: str = "fixture",
) -> dict[str, Any]:
    """Render the role's actual Compose template, then isolate its Vikunja service."""
    values = {
        **ROLE_DEFAULTS,
        **EXAMPLE_VALUES,
        "agentgateway_docker_vikunja_mcp_enabled": True,
        "agentgateway_docker_vikunja_mcp_port": port,
        "agentgateway_docker_vikunja_url": api_url,
        "agentgateway_docker_vikunja_read_token": token,
    }
    rendered = ENV.from_string(COMPOSE_TEMPLATE.read_text()).render(values)
    services = yaml.safe_load(rendered)["services"]
    sidecar = dict(services["mcp-vikunja"])

    # These are test-only Compose identities and loopback endpoints. Image,
    # command, environment keys, and application arguments come from the role.
    sidecar.pop("container_name", None)
    sidecar["ports"] = [f"127.0.0.1::{port}"]
    sidecar["networks"] = [network]
    return sidecar


def _compose_file(tmp_path: Path, jwt_secret: str, project: str) -> Path:
    config = yaml.safe_load(COMPOSE_BASE.read_text())
    config["networks"]["fixture"]["name"] = f"{project}-fixture"
    config["services"]["vikunja"]["environment"]["VIKUNJA_SERVICE_JWTSECRET"] = (
        jwt_secret
    )
    config["services"]["vikunja"]["image"] = VIKUNJA_IMAGE
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _compose(
    compose_file: Path,
    project: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(compose_file),
        *args,
    ]
    return _run(command, env=env)


def _published_url(
    compose_file: Path,
    project: str,
    service: str,
    port: int,
    *,
    env: dict[str, str] | None = None,
) -> str:
    output = _compose(
        compose_file, project, "port", service, str(port), env=env
    ).stdout.strip()
    host, mapped_port = output.rsplit(":", 1)
    assert host == "127.0.0.1", f"fixture port unexpectedly published on {host}"
    return f"http://127.0.0.1:{mapped_port}"


def _scrub(value: Any, secrets_to_hide: list[str]) -> Any:
    if isinstance(value, str):
        for secret in {*_FIXTURE_SECRETS, *secrets_to_hide}:
            value = value.replace(secret, "[redacted fixture token]")
        return value
    if isinstance(value, list):
        return [_scrub(item, secrets_to_hide) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item, secrets_to_hide) for key, item in value.items()}
    return value


def _mcp_cli_diagnostics(
    stdout: str | bytes | None, stderr: str | bytes | None
) -> dict[str, str]:
    def as_text(output: str | bytes | None) -> str:
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return (output or "").strip()

    return _scrub(
        {"stdout": as_text(stdout), "stderr": as_text(stderr)},
        _FIXTURE_SECRETS,
    )


def _wait_for_vikunja(base_url: str) -> None:
    deadline = time.monotonic() + 120
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            status, _body, _headers = _api(base_url, "GET", "/api/v1/info")
            if status == 200:
                return
            last_error = f"HTTP {status}"
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error)
        time.sleep(1)
    raise AssertionError(f"local Vikunja did not become ready: {last_error}")


def _mcp_cli(
    base_url: str,
    method: str,
    *,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    command = [
        "npx",
        "--yes",
        INSPECTOR_PACKAGE,
        "--cli",
        base_url,
        "--transport",
        "http",
        "--method",
        method,
        "--format",
        "json",
    ]
    if tool_name is not None:
        command.extend(["--tool-name", tool_name])
    if arguments is not None:
        command.extend(
            ["--tool-args-json", json.dumps(arguments, separators=(",", ":"))]
        )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        diagnostics = _mcp_cli_diagnostics(error.stdout or error.output, error.stderr)
        raise AssertionError(
            "MCP Inspector timed out; sanitized stdout/stderr="
            f"{json.dumps(diagnostics, sort_keys=True)}"
        ) from error
    try:
        parsed = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        diagnostics = _mcp_cli_diagnostics(result.stdout, result.stderr)
        raise AssertionError(
            "MCP Inspector returned non-JSON output "
            f"(exit {result.returncode}); sanitized stdout/stderr="
            f"{json.dumps(diagnostics, sort_keys=True)}"
        ) from error
    diagnostics = _mcp_cli_diagnostics(result.stdout, result.stderr)
    assert isinstance(parsed, dict), (
        "MCP Inspector returned a non-object JSON result; sanitized stdout/stderr="
        f"{json.dumps(diagnostics, sort_keys=True)}"
    )
    parsed = _scrub(parsed, _FIXTURE_SECRETS)
    if result.returncode not in (0, 5):
        print(
            f"inspector_cli_failure_diagnostics={json.dumps(diagnostics, sort_keys=True)}"
        )
    return result.returncode, parsed


def _mcp_result(envelope: dict[str, Any]) -> dict[str, Any]:
    result = envelope.get("result", envelope)
    assert isinstance(result, dict), (
        f"MCP Inspector result has unexpected shape: {envelope!r}"
    )
    return result


def _mint_token(
    base_url: str, jwt: str, title: str, permissions: dict[str, list[str]]
) -> str:
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(hours=1))
        .isoformat()
        .replace("+00:00", "Z")
    )
    status, response, _headers = _api(
        base_url,
        "PUT",
        "/api/v1/tokens",
        body={"title": title, "permissions": permissions, "expires_at": expires_at},
        token=jwt,
    )
    assert status in {200, 201}, f"token creation failed: HTTP {status}"
    token = response.get("token")
    assert isinstance(token, str) and token, (
        "Vikunja did not return the newly created token"
    )
    return token


def _provision_vikunja_fixture(
    base_url: str, *, include_write_token: bool = True
) -> dict[str, Any]:
    """Create a known task and scoped RO/RW tokens for the local API fixture."""
    username = f"{FIXTURE_USER_PREFIX}{secrets.token_hex(5)}"
    password = secrets.token_urlsafe(24)
    _FIXTURE_SECRETS.append(password)
    status, registered, _headers = _api(
        base_url,
        "POST",
        "/api/v1/register",
        body={
            "username": username,
            "email": f"{username}@example.invalid",
            "password": password,
            "language": "en",
        },
    )
    assert status in {200, 201}, (
        f"synthetic user registration failed: HTTP {status} {registered}"
    )

    status, login, _headers = _api(
        base_url,
        "POST",
        "/api/v1/login",
        body={"username": username, "password": password},
    )
    assert status == 200, f"synthetic user login failed: HTTP {status} {login}"
    jwt = login.get("token")
    assert isinstance(jwt, str) and jwt, (
        "Vikunja did not return a JWT for the fixture user"
    )
    _FIXTURE_SECRETS.append(jwt)

    status, routes, _headers = _api(base_url, "GET", "/api/v1/routes", token=jwt)
    assert status == 200, f"route catalogue lookup failed: HTTP {status} {routes}"
    task_actions = routes.get("tasks")
    assert isinstance(task_actions, dict), (
        "current route catalogue has no tasks permission group"
    )
    read_actions = sorted(
        action for action in task_actions if action.startswith("read")
    )
    assert read_actions, "current route catalogue has no task read permissions"
    assert "update" in task_actions, (
        "current route catalogue has no task update permission"
    )
    assert "update" not in read_actions, (
        "the Vikunja route catalogue misclassified update as a read action"
    )

    status, project_body, _headers = _api(
        base_url,
        "PUT",
        "/api/v1/projects",
        body={"title": "Scoped token proof project"},
        token=jwt,
    )
    assert status == 201, (
        f"synthetic project creation failed: HTTP {status} {project_body}"
    )
    project_id = project_body.get("id")
    assert isinstance(project_id, int) and project_id > 0, "project response has no id"

    status, task_body, _headers = _api(
        base_url,
        "PUT",
        f"/api/v1/projects/{project_id}/tasks",
        body={"title": FIXTURE_TASK_TITLE},
        token=jwt,
    )
    assert status == 201, f"synthetic task creation failed: HTTP {status} {task_body}"
    task_id = task_body.get("id")
    assert isinstance(task_id, int) and task_id > 0, "task response has no id"

    read_permissions = {
        group: sorted(action for action in actions if action.startswith("read"))
        for group, actions in routes.items()
        if isinstance(actions, dict)
        and any(action.startswith("read") for action in actions)
    }
    ro_token = _mint_token(base_url, jwt, "synthetic-read-only", read_permissions)
    rw_token = None
    if include_write_token:
        rw_permissions = {**read_permissions, "tasks": list(task_actions)}
        rw_token = _mint_token(base_url, jwt, "synthetic-read-write", rw_permissions)
        _FIXTURE_SECRETS.append(rw_token)
        print(
            f"Synthetic token scopes: RO tasks={read_actions}; RW tasks={sorted(task_actions)}"
        )
    else:
        print(f"Synthetic read-token scope: tasks={read_actions}")
    _FIXTURE_SECRETS.append(ro_token)
    return {
        "jwt": jwt,
        "task_id": task_id,
        "read_token": ro_token,
        "write_token": rw_token,
        "read_actions": read_actions,
        "read_permissions": read_permissions,
    }


def _call_result(envelope: dict[str, Any]) -> dict[str, Any]:
    return _mcp_result(envelope)


def _wait_for_mcp(base_url: str) -> None:
    deadline = time.monotonic() + 180
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            code, envelope = _mcp_cli(base_url, "tools/list")
            if code == 0 and isinstance(_mcp_result(envelope).get("tools"), list):
                return
            last_error = json.dumps(envelope)
        except (AssertionError, subprocess.TimeoutExpired) as error:
            last_error = str(error)
        time.sleep(2)
    raise AssertionError(f"local role sidecar did not become ready: {last_error}")
