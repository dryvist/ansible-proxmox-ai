"""Paperclip service contract for the hermes_ui_docker role."""

from pathlib import Path

import jinja2
import yaml
from jinja2 import meta


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_ui_docker"
DEFAULTS_PATH = ROLE / "defaults" / "main.yml"
TASKS_PATH = ROLE / "tasks" / "main.yml"
TEMPLATE_PATH = ROLE / "templates" / "docker-compose.yml.j2"
PAPERCLIP_REF = "v2026.1117.0"


def _load_defaults() -> dict:
    return yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))


def _render_compose(paperclip_enabled: bool) -> dict:
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_PATH.parent),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    context = {
        name: "fixture"
        for name in meta.find_undeclared_variables(env.parse(source))
    }
    context.update(
        {
            "ansible_managed": "Managed by Ansible",
            "hermes_ui_docker_workspace_service": "hermes-workspace",
            "hermes_ui_docker_workspace_image": "example/workspace:fixture",
            "hermes_ui_docker_workspace_container_name": "hermes-workspace",
            "hermes_ui_docker_workspace_port": "35001",
            "hermes_ui_docker_workspace_internal_port": 3000,
            "hermes_ui_docker_workspace_password": "fixture",
            "hermes_ui_docker_workspace_hermes_api_url": "fixture",
            "hermes_ui_docker_workspace_hermes_api_token": "",
            "hermes_ui_docker_workspace_hermes_dashboard_url": "fixture",
            "hermes_ui_docker_mission_control_service": "mission-control",
            "hermes_ui_docker_mission_control_container_name": "mission-control",
            "hermes_ui_docker_mission_control_internal_port": 3000,
            "hermes_ui_docker_mission_control_port": "35002",
            "hermes_ui_docker_mission_control_ref": "v2.3.0",
            "hermes_ui_docker_mission_control_gateway_optional": "true",
            "hermes_ui_docker_mission_control_data_dir": "/var/lib/hermes-ui/mission-control",
            "hermes_ui_docker_mission_control_allowed_hosts": "fixture,localhost,127.0.0.1",
            "hermes_ui_docker_mission_control_auth_user": "fixture",
            "hermes_ui_docker_mission_control_auth_pass": "fixture",
            "hermes_ui_docker_restart_policy": "unless-stopped",
            "hermes_ui_docker_paperclip_enabled": paperclip_enabled,
            "hermes_ui_docker_paperclip_service": "paperclip",
            "hermes_ui_docker_paperclip_container_name": "paperclip",
            "hermes_ui_docker_paperclip_port": "35003",
            "hermes_ui_docker_paperclip_internal_port": 3100,
            "hermes_ui_docker_paperclip_data_dir": "/var/lib/hermes-ui/paperclip",
            "hermes_ui_docker_paperclip_home": "/paperclip",
            "hermes_ui_docker_paperclip_deployment_mode": "authenticated",
            "hermes_ui_docker_paperclip_deployment_exposure": "private",
            "hermes_ui_docker_paperclip_telemetry_disabled": "1",
            "hermes_ui_docker_paperclip_do_not_track": "1",
        }
    )
    rendered = env.from_string(source).render(**context)
    return yaml.safe_load(rendered)


def _expected_mission_control() -> dict:
    return {
        "build": {
            "context": "./mission-control",
            "args": {
                "MC_VERSION": "v2.3.0",
                "NEXT_PUBLIC_GATEWAY_OPTIONAL": "true",
            },
        },
        "image": "homelab/mission-control:local",
        "container_name": "mission-control",
        "restart": "unless-stopped",
        "ports": ["35002:3000"],
        "volumes": ["/var/lib/hermes-ui/mission-control:/app/.data"],
        "environment": {
            "PORT": "3000",
            "MC_ALLOWED_HOSTS": "fixture,localhost,127.0.0.1",
            "MC_COOKIE_SECURE": "1",
            "MC_ENABLE_HSTS": "1",
            "AUTH_USER": "fixture",
            "AUTH_PASS": "fixture",
        },
    }


def test_paperclip_is_off_by_default_and_mission_control_is_unchanged() -> None:
    defaults = _load_defaults()
    assert defaults["hermes_ui_docker_paperclip_enabled"] is False

    services = _render_compose(paperclip_enabled=False)["services"]
    assert "paperclip" not in services
    assert services["mission-control"] == _expected_mission_control()


def test_enabled_paperclip_renders_pinned_private_persistent_service() -> None:
    defaults = _load_defaults()
    assert defaults["hermes_ui_docker_paperclip_ref"] == PAPERCLIP_REF
    port_default = defaults["hermes_ui_docker_paperclip_port"]
    assert "hermes_ui_paperclip" in port_default
    assert "mandatory" in port_default

    tasks = yaml.safe_load(TASKS_PATH.read_text(encoding="utf-8"))
    clone = next(
        task
        for task in tasks
        if task.get("name") == "Clone Paperclip at the pinned tag"
    )
    clone_args = clone["ansible.builtin.git"]
    assert clone_args["repo"] == "{{ hermes_ui_docker_paperclip_repo }}"
    assert clone_args["dest"] == "{{ hermes_ui_docker_data_dir }}/paperclip"
    assert clone_args["version"] == "{{ hermes_ui_docker_paperclip_ref }}"
    assert "depth" not in clone_args

    compose = _render_compose(paperclip_enabled=True)
    services = compose["services"]
    assert set(services) == {"hermes-workspace", "mission-control", "paperclip"}
    assert services["mission-control"] == _expected_mission_control()

    paperclip = services["paperclip"]
    assert paperclip["build"]["context"] == "./paperclip"
    assert paperclip["ports"] == ["35003:3100"]
    assert paperclip["volumes"] == ["/var/lib/hermes-ui/paperclip:/paperclip"]

    environment = paperclip["environment"]
    assert environment["PORT"] == "3100"
    assert environment["PAPERCLIP_HOME"] == "/paperclip"
    assert environment["PAPERCLIP_DEPLOYMENT_MODE"] == "authenticated"
    assert environment["PAPERCLIP_DEPLOYMENT_EXPOSURE"] == "private"
    assert environment["PAPERCLIP_TELEMETRY_DISABLED"] == "1"
    assert environment["DO_NOT_TRACK"] == "1"
    assert "DATABASE_URL" not in environment
    assert not any(
        key.startswith(("OTEL_", "SENTRY_DSN"))
        for key in environment
    )
    assert not any("gateway" in name.lower() for name in services)

    healthcheck = paperclip["healthcheck"]
    health_command = " ".join(str(part) for part in healthcheck["test"])
    assert "/api/health" in health_command
