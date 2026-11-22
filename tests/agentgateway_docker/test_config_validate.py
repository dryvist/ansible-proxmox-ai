"""The config task checks the staged render with the pinned image before it replaces the live file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from test_authz_render import ENV

TASKS = Path(__file__).resolve().parents[2] / "roles/agentgateway_docker/tasks/main.yml"
IMAGE = "ghcr.io/example/agentgateway:v0.0.0"
BASE: dict[str, Any] = {
    "agentgateway_docker_image": IMAGE,
    "agentgateway_docker_nautobot_openapi_enabled": False,
    "agentgateway_docker_nautobot_read_token": "",
    "agentgateway_docker_nautobot_openapi_schema_file": "/opt/agentgateway/schema.json",
    "agentgateway_docker_nautobot_openapi_schema_path": "/nautobot-openapi.json",
    "agentgateway_docker_nautobot_openapi_token_file": "/opt/agentgateway/nautobot-token",
    "agentgateway_docker_nautobot_openapi_token_path": "/nautobot-token",
    "agentgateway_docker_proxied_mcp_targets": [],
    "agentgateway_docker_proxied_mcp_secrets_dir": "/opt/agentgateway/proxied-mcp-secrets",
    "agentgateway_docker_proxied_mcp_secrets_path": "/proxied-mcp-secrets",
}


def _validate(**overrides: Any) -> list[str]:
    task = next(t for t in yaml.safe_load(TASKS.read_text()) if t["name"] == "Deploy agentgateway config")
    return ENV.from_string(task["ansible.builtin.template"]["validate"]).render(**(BASE | overrides)).split()


def test_validate_runs_the_pinned_image_on_the_staged_file() -> None:
    assert _validate() == [
        "docker", "run", "--rm", "--network", "none", "--user", "0:0",
        "-v", "%s:/config.yaml:ro",
        IMAGE, "--validate-only", "-f", "/config.yaml",
    ]


def test_validate_mounts_every_file_the_config_names() -> None:
    args = _validate(
        agentgateway_docker_nautobot_openapi_enabled=True,
        agentgateway_docker_nautobot_read_token="example-token",
        agentgateway_docker_proxied_mcp_targets=[{"name": "example"}],
    )
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    assert mounts == [
        "%s:/config.yaml:ro",
        "/opt/agentgateway/schema.json:/nautobot-openapi.json:ro",
        "/opt/agentgateway/nautobot-token:/nautobot-token:ro",
        "/opt/agentgateway/proxied-mcp-secrets:/proxied-mcp-secrets:ro",
    ]
    assert args[-4:] == [IMAGE, "--validate-only", "-f", "/config.yaml"]
