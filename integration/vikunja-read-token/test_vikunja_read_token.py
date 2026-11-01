"""Prove Vikunja's scoped API token blocks a real MCP task update."""

from __future__ import annotations

import json
import secrets
import shutil
import uuid
from pathlib import Path

import yaml
from vikunja_read_token_support import (
    _FIXTURE_SECRETS,
    FIXTURE_TASK_TITLE,
    INSPECTOR_PACKAGE,
    ROLE_DEFAULTS,
    VIKUNJA_IMAGE,
    _api,
    _call_result,
    _compose,
    _compose_file,
    _mcp_cli,
    _mcp_result,
    _provision_vikunja_fixture,
    _published_url,
    _render_sidecar,
    _run,
    _scrub,
    _wait_for_mcp,
    _wait_for_vikunja,
)

DENIED_TASK_TITLE = "This edit must be denied"
ALLOWED_TASK_TITLE = "RW positive-control edit"


def test_read_token_allows_read_and_denies_real_sidecar_write(tmp_path: Path) -> None:
    for executable in ("docker", "npx"):
        assert shutil.which(executable), (
            f"{executable} is required for this integration fixture"
        )

    project = f"vikunja-ro-{uuid.uuid4().hex[:12]}"
    jwt_secret = secrets.token_urlsafe(48)
    _FIXTURE_SECRETS.append(jwt_secret)
    compose_file = _compose_file(tmp_path, jwt_secret, project)
    try:
        _compose(compose_file, project, "up", "--detach", "vikunja")
        vikunja_url = _published_url(compose_file, project, "vikunja", 3456)
        _wait_for_vikunja(vikunja_url)

        fixture = _provision_vikunja_fixture(vikunja_url)
        jwt = fixture["jwt"]
        task_id = fixture["task_id"]
        ro_token = fixture["read_token"]
        rw_token = fixture["write_token"]
        assert isinstance(rw_token, str), (
            "Vikunja fixture did not create its RW positive-control token"
        )
        read_actions = fixture["read_actions"]
        read_permissions = fixture["read_permissions"]
        tokens = [ro_token, rw_token, jwt]

        config = yaml.safe_load(compose_file.read_text())
        for service_name, token, port in (
            ("mcp-vikunja-ro", ro_token, 8002),
            ("mcp-vikunja-rw", rw_token, 8003),
        ):
            service = _render_sidecar(token=token, port=port)
            config["services"][service_name] = service
        compose_file.write_text(yaml.safe_dump(config, sort_keys=False))

        _compose(
            compose_file, project, "up", "--detach", "mcp-vikunja-ro", "mcp-vikunja-rw"
        )
        ro_url = _published_url(compose_file, project, "mcp-vikunja-ro", 8002) + "/mcp"
        rw_url = _published_url(compose_file, project, "mcp-vikunja-rw", 8003) + "/mcp"

        # The role's native npx command may use network access from this regular
        # fixture bridge; this test does not claim egress denial.
        _wait_for_mcp(ro_url)
        _wait_for_mcp(rw_url)

        list_code, list_envelope = _mcp_cli(ro_url, "tools/list")
        assert list_code == 0, f"read-only sidecar tools/list failed: {list_envelope}"
        tools = _mcp_result(list_envelope).get("tools", [])
        task_tool = next(
            (tool for tool in tools if tool.get("name") == "vikunja_tasks"), None
        )
        assert isinstance(task_tool, dict), "role sidecar did not expose vikunja_tasks"
        subcommands = (
            task_tool.get("inputSchema", {})
            .get("properties", {})
            .get("subcommand", {})
            .get("enum", [])
        )
        assert "get" in subcommands and "update" in subcommands, (
            "task get/update tools are not exposed"
        )

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
        assert (
            before_status == 200 and before_task.get("title") == FIXTURE_TASK_TITLE
        ), (
            f"fixture task state unexpected before denial: HTTP {before_status} {before_task}"
        )

        denied_code, denied_envelope = _mcp_cli(
            ro_url,
            "tools/call",
            tool_name="vikunja_tasks",
            arguments={
                "subcommand": "update",
                "id": task_id,
                "title": DENIED_TASK_TITLE,
            },
        )
        denied_result = _call_result(denied_envelope)
        assert denied_code == 5 and denied_result.get("isError") is True, (
            f"RO update did not return an MCP tool error: exit={denied_code} result={denied_envelope}"
        )
        error_content = denied_result.get("content")
        assert isinstance(error_content, list) and error_content, (
            f"RO update error did not include the exact MCP error payload: {denied_envelope}"
        )
        print(
            f"Read-only update denial: {json.dumps(_scrub(denied_envelope, tokens), sort_keys=True)}"
        )

        after_denial_status, after_denial_task, _headers = _api(
            vikunja_url, "GET", f"/api/v1/tasks/{task_id}", token=jwt
        )
        assert (
            after_denial_status == 200
            and after_denial_task.get("title") == FIXTURE_TASK_TITLE
        ), (
            "independent Vikunja API read shows the denied MCP update changed task state: "
            f"HTTP {after_denial_status} {after_denial_task}"
        )

        rw_code, rw_envelope = _mcp_cli(
            rw_url,
            "tools/call",
            tool_name="vikunja_tasks",
            arguments={
                "subcommand": "update",
                "id": task_id,
                "title": ALLOWED_TASK_TITLE,
            },
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
        assert isinstance(info.get("version"), str) and info["version"], (
            "Vikunja did not report its version"
        )
        sidecar_image = config["services"]["mcp-vikunja-ro"]["image"]
        identities = {
            name: _run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format={{.Id}}/{{.Os}}/{{.Architecture}}",
                    image,
                ]
            ).stdout.strip()
            for name, image in {
                "vikunja": VIKUNJA_IMAGE,
                "sidecar": sidecar_image,
            }.items()
        }
        receipt = {
            "acceptance_status": "passed",
            "scope": "local fixture; not a live deployment receipt",
            "vikunja_image": VIKUNJA_IMAGE,
            "vikunja_version": info.get("version"),
            "image_identities": identities,
            "sidecar_image": sidecar_image,
            "sidecar_package_version": ROLE_DEFAULTS[
                "agentgateway_docker_vikunja_mcp_version"
            ],
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
