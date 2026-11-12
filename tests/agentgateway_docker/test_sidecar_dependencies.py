"""FastMCP sidecars install every third-party package their server scripts import.

Import names are compared with distribution names, which match for every package
these sidecars use (httpx, fastmcp).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_authz_render import ENV, ROLE_DEFAULTS

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_TEMPLATE = REPO_ROOT / "roles/agentgateway_docker/templates/docker-compose.yml.j2"
VIKUNJA_CONFIG_TEMPLATE = REPO_ROOT / "roles/agentgateway_docker/templates/vikunja-clerk-config.json.j2"
AGENTGATEWAY_TASKS = REPO_ROOT / "roles/agentgateway_docker/tasks/main.yml"
SIDECAR_FILES = REPO_ROOT / "roles/agentgateway_docker/files"
SIDECARS = {
    "mcp-docs": "mcp_docs_server.py",
    "mcp-nautobot": "mcp_nautobot_server.py",
}
HTTPX_PIN = f"httpx=={ROLE_DEFAULTS['agentgateway_docker_mcp_sidecar_httpx_version']}"

# Defaults that are Jinja expressions over play hostvars, env and Vault lookups are replaced
# with inert examples, so the template renders without an inventory or a live secret.
EXAMPLE_VALUES: dict[str, Any] = {
    "ansible_managed": "managed by test",
    "agentgateway_docker_admin_port": 15001,
    "agentgateway_docker_metrics_port": 15002,
    "agentgateway_docker_proxy_port": 15000,
    "agentgateway_docker_docs_mcp_router_base_url": "https://llm.example.com/v1",
    "agentgateway_docker_docs_mcp_router_api_key": "example-router-key",
    "agentgateway_docker_qdrant_url": "http://qdrant.example.com:6333",
    "agentgateway_docker_qdrant_api_key": "example-docs-key",
    "agentgateway_docker_qdrant_memory_mcp_api_key": "example-memory-key",
    "agentgateway_docker_nautobot_url": "https://nautobot.example.com",
    "agentgateway_docker_nautobot_read_token": "example-nautobot-token",
    "agentgateway_docker_nautobot_openapi_enabled": False,
    "agentgateway_docker_nautobot_legacy_env_file": "/opt/agentgateway/nautobot.env",
    "agentgateway_docker_vikunja_url": "https://vikunja.example.com/api/v1",
    "agentgateway_docker_vikunja_read_token": "example-vikunja-token",
    "agentgateway_docker_vikunja_clerk_enabled": True,
    "agentgateway_docker_vikunja_clerk_api_token": "",
    "agentgateway_docker_vikunja_clerk_http_token": "",
    "agentgateway_docker_vikunja_clerk_port": 8004,
    "agentgateway_docker_vikunja_clerk_allowed_host": "mcp-vikunja-clerk:8004",
    "agentgateway_docker_vikunja_clerk_image": (
        "ghcr.io/netadvanced/vikunja-mcp-ng:0.8.0-beta.0"
        "@sha256:5b65b7f06ce85e209dd8a2360bfb433588ed0e6bf9b375e97597ed3277daf4ee"
    ),
    "agentgateway_docker_vikunja_clerk_api_token_file": "/run/secrets/vikunja-api-token",
    "agentgateway_docker_vikunja_clerk_http_token_file": "/run/secrets/mcp-http-token",
    "agentgateway_docker_vikunja_clerk_config_file": "/etc/vikunja-mcp/config.json",
}


def _services(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    rendered = ENV.from_string(COMPOSE_TEMPLATE.read_text()).render(
        {**ROLE_DEFAULTS, **EXAMPLE_VALUES, **(extra or {})}
    )
    return yaml.safe_load(rendered)["services"]


def _with_specs(command: list[str]) -> list[str]:
    """Package specs that follow each `--with` in a `uv run` command."""
    return [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "--with"]


def _package(spec: str) -> str:
    return re.split(r"[=<>!~\[ ]", spec, maxsplit=1)[0].lower()


@pytest.mark.parametrize("service", list(SIDECARS))
def test_sidecar_command_installs_the_pinned_httpx(service: str) -> None:
    assert HTTPX_PIN in _with_specs(_services()[service]["command"])


@pytest.mark.parametrize(("service", "script"), list(SIDECARS.items()))
def test_every_third_party_import_is_installed(service: str, script: str) -> None:
    tree = ast.parse((SIDECAR_FILES / script).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    third_party = imported - set(sys.stdlib_module_names) - {"fastmcp"}
    assert third_party, f"{script} has no third-party imports, so the check would pass vacuously"
    installed = {_package(spec) for spec in _with_specs(_services()[service]["command"])}
    assert not third_party - installed, f"{service} does not install {sorted(third_party - installed)}"


def test_vikunja_clerk_uses_native_http_and_file_backed_credentials() -> None:
    token = "x" * 64
    clerk = _services(
        {
            "agentgateway_docker_vikunja_clerk_api_token": "fixture-vikunja-api-token",
            "agentgateway_docker_vikunja_clerk_http_token": token,
        }
    )["mcp-vikunja-clerk"]

    assert clerk["image"] == (
        "ghcr.io/netadvanced/vikunja-mcp-ng:0.8.0-beta.0"
        "@sha256:5b65b7f06ce85e209dd8a2360bfb433588ed0e6bf9b375e97597ed3277daf4ee"
    )
    assert clerk["environment"]["VIKUNJA_MCP_TRANSPORT"] == "http"
    assert clerk["environment"]["VIKUNJA_MCP_HTTP_AUTH_MODE"] == "token"
    assert clerk["environment"]["VIKUNJA_API_TOKEN_FILE"] == "/run/secrets/vikunja-api-token"
    assert clerk["environment"]["VIKUNJA_MCP_HTTP_AUTH_TOKEN_FILE"] == "/run/secrets/mcp-http-token"
    assert clerk["environment"]["VIKUNJA_MCP_HTTP_ALLOWED_HOSTS"] == "mcp-vikunja-clerk:8004"
    assert "VIKUNJA_API_TOKEN" not in clerk["environment"]
    assert "VIKUNJA_MCP_HTTP_AUTH_TOKEN" not in clerk["environment"]
    assert not clerk.get("ports")
    assert len(clerk["volumes"]) == 3


def test_vikunja_clerk_role_default_image_is_digest_pinned() -> None:
    assert ROLE_DEFAULTS["agentgateway_docker_vikunja_clerk_image"] == (
        "ghcr.io/netadvanced/vikunja-mcp-ng:0.8.0-beta.0"
        "@sha256:5b65b7f06ce85e209dd8a2360bfb433588ed0e6bf9b375e97597ed3277daf4ee"
    )


def test_vikunja_clerk_tokens_are_trimmed_and_secret_tasks_are_hidden() -> None:
    secrets = {
        "VIKUNJA_MCP_TOKEN_VIKUNJA_CLERK": "api-fixture-token\n",
        "VIKUNJA_MCP_HTTP_TOKEN_VIKUNJA_CLERK": "http-fixture-token\n",
    }
    for name, field in (
        ("agentgateway_docker_vikunja_clerk_api_token", "VIKUNJA_MCP_TOKEN_VIKUNJA_CLERK"),
        ("agentgateway_docker_vikunja_clerk_http_token", "VIKUNJA_MCP_HTTP_TOKEN_VIKUNJA_CLERK"),
    ):
        assert ENV.from_string(ROLE_DEFAULTS[name]).render(bao_local_llm_secrets=secrets) == secrets[
            field
        ].strip()

    main_tasks = yaml.safe_load(AGENTGATEWAY_TASKS.read_text())
    clerk_include = next(
        task["ansible.builtin.include_tasks"]
        for task in main_tasks
        if task.get("ansible.builtin.include_tasks") == "vikunja_clerk.yml"
    )
    clerk_tasks = yaml.safe_load((AGENTGATEWAY_TASKS.parent / clerk_include).read_text())
    tasks = [*main_tasks, *clerk_tasks]
    credential_tasks = [
        task
        for task in tasks
        if "agentgateway_docker_vikunja_clerk_api_token" in str(task)
        or "agentgateway_docker_vikunja_clerk_http_token" in str(task)
    ]
    assert credential_tasks
    assert all(task.get("no_log") is True for task in credential_tasks)


def test_vikunja_clerk_config_exposes_only_the_tasks_module() -> None:
    rendered = ENV.from_string(VIKUNJA_CONFIG_TEMPLATE.read_text()).render()
    config = yaml.safe_load(rendered)

    assert config["readOnly"] is False
    assert config["modules"]["tasks"] is True
    assert all(not enabled for name, enabled in config["modules"].items() if name != "tasks")
