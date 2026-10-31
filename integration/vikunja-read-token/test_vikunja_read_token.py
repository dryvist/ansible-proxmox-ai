"""Prove Vikunja's scoped API token blocks a real MCP task update."""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
import yaml

from test_authz_render import ENV, ROLE_DEFAULTS
from test_sidecar_dependencies import COMPOSE_TEMPLATE, EXAMPLE_VALUES

COMPOSE_BASE = Path(__file__).with_name("compose.yml")
VIKUNJA_IMAGE = (
    "vikunja/vikunja:2.7.1@sha256:"
    "f7dc8428d10aef17f3075886d3c1c022f566ec456f88985f02c137f4fc534576"
)
INSPECTOR_PACKAGE = "@modelcontextprotocol/inspector@2.12.0"
_FIXTURE_SECRETS: list[str] = []
FIXTURE_USER_PREFIX = "vikunja-ro-falsifier-"
FIXTURE_TASK_TITLE = "Scoped token proof fixture"
DENIED_TASK_TITLE = "This edit must be denied"
ALLOWED_TASK_TITLE = "RW positive-control edit"
API_TIMEOUT_SECONDS = 15
COMMAND_TIMEOUT_SECONDS = 90


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    # Compose may surface container diagnostics. Keep them out of pytest
    # failures because they can include environment values from this fixture.
    assert result.returncode == 0, f"command failed: {command[0]} (exit {result.returncode})"
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
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as response:
        data = response.read()
        status = response.code
        response_headers = {key.lower(): value for key, value in response.headers.items()}
    parsed = json.loads(data) if data else {}
    assert isinstance(parsed, dict), f"Vikunja returned a non-object response for {method} {path}"
    return status, parsed, response_headers


def _render_sidecar(*, token: str, port: int) -> dict[str, Any]:
    """Render the role's actual Compose template, then isolate its Vikunja service."""
    values = {
        **ROLE_DEFAULTS,
        **EXAMPLE_VALUES,
        "agentgateway_docker_vikunja_mcp_enabled": True,
        "agentgateway_docker_vikunja_mcp_port": port,
        "agentgateway_docker_vikunja_url": "http://vikunja:3456/api/v1",
        "agentgateway_docker_vikunja_read_token": token,
    }
    rendered = ENV.from_string(COMPOSE_TEMPLATE.read_text()).render(values)
    services = yaml.safe_load(rendered)["services"]
    sidecar = dict(services["mcp-vikunja"])

    # These are test-only Compose identities and loopback endpoints. Image,
    # command, environment keys, and application arguments come from the role.
    sidecar.pop("container_name", None)
    sidecar["ports"] = [f"127.0.0.1::{port}"]
    sidecar["networks"] = ["fixture"]
    return sidecar


def _compose_file(tmp_path: Path, jwt_secret: str, project: str) -> Path:
    config = yaml.safe_load(COMPOSE_BASE.read_text())
    config["networks"]["fixture"]["name"] = f"{project}-fixture"
    config["services"]["vikunja"]["environment"]["VIKUNJA_SERVICE_JWTSECRET"] = jwt_secret
    config["services"]["vikunja"]["image"] = VIKUNJA_IMAGE
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def _compose(compose_file: Path, project: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--project-name", project, "--file", str(compose_file), *args]
    return _run(command)


def _published_url(compose_file: Path, project: str, service: str, port: int) -> str:
    output = _compose(compose_file, project, "port", service, str(port)).stdout.strip()
    host, mapped_port = output.rsplit(":", 1)
    assert host == "127.0.0.1", f"fixture port unexpectedly published on {host}"
    return f"http://127.0.0.1:{mapped_port}"


def _mcp_cli_diagnostics(stdout: str | bytes | None, stderr: str | bytes | None) -> dict[str, str]:
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
        command.extend(["--tool-args-json", json.dumps(arguments, separators=(",", ":"))])
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
        print(f"inspector_cli_failure_diagnostics={json.dumps(diagnostics, sort_keys=True)}")
    return result.returncode, parsed


def _mcp_result(envelope: dict[str, Any]) -> dict[str, Any]:
    result = envelope.get("result", envelope)
    assert isinstance(result, dict), f"MCP Inspector result has unexpected shape: {envelope!r}"
    return result


def _mint_token(base_url: str, jwt: str, title: str, permissions: dict[str, list[str]]) -> str:
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    status, response, _headers = _api(
        base_url,
        "PUT",
        "/api/v1/tokens",
        body={"title": title, "permissions": permissions, "expires_at": expires_at},
        token=jwt,
    )
    assert status in {200, 201}, f"token creation failed: HTTP {status}"
    token = response.get("token")
    assert isinstance(token, str) and token, "Vikunja did not return the newly created token"
    return token


def _call_result(envelope: dict[str, Any]) -> dict[str, Any]:
    return _mcp_result(envelope)


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


def test_read_token_allows_read_and_denies_real_sidecar_write(tmp_path: Path) -> None:
    for executable in ("docker", "npx"):
        assert shutil.which(executable), f"{executable} is required for this integration fixture"

    project = f"vikunja-ro-{uuid.uuid4().hex[:12]}"
    jwt_secret = secrets.token_urlsafe(48)
    _FIXTURE_SECRETS.append(jwt_secret)
    compose_file = _compose_file(tmp_path, jwt_secret, project)
    try:
        _compose(compose_file, project, "up", "--detach", "vikunja")
        vikunja_url = _published_url(compose_file, project, "vikunja", 3456)
        _wait_for_vikunja(vikunja_url)

        username = f"{FIXTURE_USER_PREFIX}{secrets.token_hex(5)}"
        password = secrets.token_urlsafe(24)
        _FIXTURE_SECRETS.append(password)
        status, registered, _headers = _api(
            vikunja_url,
            "POST",
            "/api/v1/register",
            body={
                "username": username,
                "email": f"{username}@example.invalid",
                "password": password,
                "language": "en",
            },
        )
        assert status in {200, 201}, f"synthetic user registration failed: HTTP {status} {registered}"

        status, login, _headers = _api(
            vikunja_url,
            "POST",
            "/api/v1/login",
            body={"username": username, "password": password},
        )
        assert status == 200, f"synthetic user login failed: HTTP {status} {login}"
        jwt = login.get("token")
        assert isinstance(jwt, str) and jwt, "Vikunja did not return a JWT for the fixture user"

        status, routes, _headers = _api(vikunja_url, "GET", "/api/v1/routes", token=jwt)
        assert status == 200, f"route catalogue lookup failed: HTTP {status} {routes}"
        task_actions = routes.get("tasks")
        assert isinstance(task_actions, dict), "current route catalogue has no tasks permission group"
        read_actions = sorted(action for action in task_actions if action.startswith("read"))
        assert read_actions, "current route catalogue has no task read permissions"
        assert "update" in task_actions, "current route catalogue has no task update permission"
        assert "update" not in read_actions, "the Vikunja route catalogue misclassified update as a read action"

        status, project_body, _headers = _api(
            vikunja_url,
            "PUT",
            "/api/v1/projects",
            body={"title": "Scoped token proof project"},
            token=jwt,
        )
        assert status == 201, f"synthetic project creation failed: HTTP {status} {project_body}"
        project_id = project_body.get("id")
        assert isinstance(project_id, int) and project_id > 0, "project response has no id"

        status, task_body, _headers = _api(
            vikunja_url,
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
            if isinstance(actions, dict) and any(action.startswith("read") for action in actions)
        }
        ro_token = _mint_token(vikunja_url, jwt, "synthetic-read-only", read_permissions)
        rw_permissions = {**read_permissions, "tasks": list(task_actions)}
        rw_token = _mint_token(vikunja_url, jwt, "synthetic-read-write", rw_permissions)
        tokens = [ro_token, rw_token, jwt]
        _FIXTURE_SECRETS.extend(tokens)
        print(f"Synthetic token scopes: RO tasks={read_actions}; RW tasks={sorted(task_actions)}")

        config = yaml.safe_load(compose_file.read_text())
        for service_name, token, port in (
            ("mcp-vikunja-ro", ro_token, 8002),
            ("mcp-vikunja-rw", rw_token, 8003),
        ):
            service = _render_sidecar(token=token, port=port)
            config["services"][service_name] = service
        compose_file.write_text(yaml.safe_dump(config, sort_keys=False))

        _compose(compose_file, project, "up", "--detach", "mcp-vikunja-ro", "mcp-vikunja-rw")
        ro_url = _published_url(compose_file, project, "mcp-vikunja-ro", 8002) + "/mcp"
        rw_url = _published_url(compose_file, project, "mcp-vikunja-rw", 8003) + "/mcp"

        # The role's native npx command may use network access from this regular
        # fixture bridge; this test does not claim egress denial.
        _wait_for_mcp(ro_url)
        _wait_for_mcp(rw_url)

        list_code, list_envelope = _mcp_cli(ro_url, "tools/list")
        assert list_code == 0, f"read-only sidecar tools/list failed: {list_envelope}"
        tools = _mcp_result(list_envelope).get("tools", [])
        task_tool = next((tool for tool in tools if tool.get("name") == "vikunja_tasks"), None)
        assert isinstance(task_tool, dict), "role sidecar did not expose vikunja_tasks"
        subcommands = task_tool.get("inputSchema", {}).get("properties", {}).get("subcommand", {}).get("enum", [])
        assert "get" in subcommands and "update" in subcommands, "task get/update tools are not exposed"

        get_code, get_envelope = _mcp_cli(
            ro_url,
            "tools/call",
            tool_name="vikunja_tasks",
            arguments={"subcommand": "get", "id": task_id},
        )
        get_result = _call_result(get_envelope)
        assert get_code == 0 and get_result.get("isError") is not True, (
            f"read-only token could not read its owned task: {get_envelope}"
        )
        assert FIXTURE_TASK_TITLE in json.dumps(get_result), (
            "read-only tool response did not contain the known fixture task"
        )

        before_status, before_task, _headers = _api(
            vikunja_url, "GET", f"/api/v1/tasks/{task_id}", token=jwt
        )
        assert before_status == 200 and before_task.get("title") == FIXTURE_TASK_TITLE, (
            f"fixture task state unexpected before denial: HTTP {before_status} {before_task}"
        )

        denied_code, denied_envelope = _mcp_cli(
            ro_url,
            "tools/call",
            tool_name="vikunja_tasks",
            arguments={"subcommand": "update", "id": task_id, "title": DENIED_TASK_TITLE},
        )
        denied_result = _call_result(denied_envelope)
        assert denied_code == 5 and denied_result.get("isError") is True, (
            f"RO update did not return an MCP tool error: exit={denied_code} result={denied_envelope}"
        )
        error_content = denied_result.get("content")
        assert isinstance(error_content, list) and error_content, (
            f"RO update error did not include the exact MCP error payload: {denied_envelope}"
        )
        print(f"Read-only update denial: {json.dumps(_scrub(denied_envelope, tokens), sort_keys=True)}")

        after_denial_status, after_denial_task, _headers = _api(
            vikunja_url, "GET", f"/api/v1/tasks/{task_id}", token=jwt
        )
        assert after_denial_status == 200 and after_denial_task.get("title") == FIXTURE_TASK_TITLE, (
            "independent Vikunja API read shows the denied MCP update changed task state: "
            f"HTTP {after_denial_status} {after_denial_task}"
        )

        rw_code, rw_envelope = _mcp_cli(
            rw_url,
            "tools/call",
            tool_name="vikunja_tasks",
            arguments={"subcommand": "update", "id": task_id, "title": ALLOWED_TASK_TITLE},
        )
        rw_result = _call_result(rw_envelope)
        assert rw_code == 0 and rw_result.get("isError") is not True, (
            f"RW same-path update positive control failed: {rw_envelope}"
        )
        final_status, final_task, _headers = _api(
            vikunja_url, "GET", f"/api/v1/tasks/{task_id}", token=jwt
        )
        assert final_status == 200 and final_task.get("title") == ALLOWED_TASK_TITLE, (
            f"RW tool success did not persist through Vikunja: HTTP {final_status} {final_task}"
        )
        info_status, info, _headers = _api(vikunja_url, "GET", "/api/v1/info")
        assert info_status == 200, "could not record the fixture's Vikunja version"
        assert isinstance(info.get("version"), str) and info["version"], "Vikunja did not report its version"
        sidecar_image = config["services"]["mcp-vikunja-ro"]["image"]
        identities = {
            name: _run([
                "docker", "image", "inspect", "--format={{.Id}}/{{.Os}}/{{.Architecture}}", image
            ]).stdout.strip()
            for name, image in {"vikunja": VIKUNJA_IMAGE, "sidecar": sidecar_image}.items()
        }
        receipt = {
            "acceptance_status": "passed",
            "scope": "local fixture; not a live deployment receipt",
            "vikunja_image": VIKUNJA_IMAGE,
            "vikunja_version": info.get("version"),
            "image_identities": identities,
            "sidecar_image": sidecar_image,
            "sidecar_package_version": ROLE_DEFAULTS["agentgateway_docker_vikunja_mcp_version"],
            "inspector_package": INSPECTOR_PACKAGE,
            "read_token_actions": read_actions,
            "read_token_permissions": read_permissions,
            "write_tool": "vikunja_tasks.update",
            "write_error": _scrub(denied_envelope, tokens),
            "ro_read": "passed",
            "ro_write_denial_and_unchanged_state": "passed",
            "rw_same_path_write_and_readback": "passed",
        }
        print(f"vikunja_read_token_receipt={json.dumps(receipt, sort_keys=True)}")
    finally:
        try:
            _compose(compose_file, project, "down", "--volumes", "--remove-orphans")
        finally:
            compose_file.unlink(missing_ok=True)


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
