"""The Hindsight API and its control plane share the generated tenant key."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

from _compose_render import env_line, render

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_api_and_control_plane_use_the_tenant_extension_key() -> None:
    rendered = render()
    assert "hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension" in env_line(
        rendered, "HINDSIGHT_API_TENANT_EXTENSION"
    )
    assert '"test-hindsight-api-key"' in env_line(rendered, "HINDSIGHT_API_TENANT_API_KEY")
    assert '"test-hindsight-api-key"' in env_line(rendered, "HINDSIGHT_CP_DATAPLANE_API_KEY")


def test_compose_render_fails_without_the_api_key() -> None:
    try:
        render(include_api_key=False, strict=True)
    except jinja2.UndefinedError:
        return
    raise AssertionError("Compose rendered without the mandatory Hindsight API key")


def test_hindsight_health_probe_stays_unauthenticated() -> None:
    tasks = yaml.safe_load((REPO_ROOT / "roles/hindsight_docker/tasks/main.yml").read_text())
    health = next(task for task in tasks if task.get("name") == "Verify hindsight health endpoint")
    request = health["ansible.builtin.uri"]
    assert request["method"] == "GET"
    assert request["status_code"] == 200
    assert request["url"].endswith("/health")
    assert "headers" not in request
