"""Hermes Langfuse tracing is enabled only with both credentials."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
TEMPLATES = ROLE_ROOT / "templates"


def _jinja_env() -> Environment:
    env = Environment(autoescape=False, loader=FileSystemLoader(TEMPLATES))
    env.filters["bool"] = bool
    return env


def _enabled(public_key: str, secret_key: str) -> bool:
    expression = role_defaults(ROLE_ROOT)["hermes_agent_langfuse_enabled"]
    rendered = _jinja_env().from_string(str(expression)).render(
        hermes_agent_langfuse_public_key=public_key,
        hermes_agent_langfuse_secret_key=secret_key,
    )
    return rendered.strip().lower() in {"true", "yes", "1"}


def _env_values(rendered: str) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in rendered.splitlines()
        if "=" in line and not line.startswith("#")
    )


def test_langfuse_requires_both_keys_and_renders_sanitized_config() -> None:
    cases = (
        ("", "", False),
        ("pk-lf-test", "", False),
        ("", "sk-lf-test", False),
        ("pk-lf-test", "sk-lf-test", True),
    )
    config = _jinja_env().get_template("config-langfuse-observability.yaml.j2")
    env_template = _jinja_env().get_template("hermes-env-langfuse.j2")

    for public_key, secret_key, expected in cases:
        enabled = _enabled(public_key, secret_key)
        assert enabled is expected
        context = {
            "hermes_agent_langfuse_enabled": enabled,
            "hermes_agent_langfuse_public_key": public_key,
            "hermes_agent_langfuse_secret_key": secret_key,
            "hermes_agent_langfuse_base_url": "https://langfuse.example.test",
        }

        plugin_config = yaml.safe_load(config.render(**context)) or {}
        plugins = plugin_config.get("plugins", {}).get("enabled", [])
        assert ("observability/langfuse" in plugins) is expected

        values = _env_values(env_template.render(**context))
        langfuse_values = {
            key: value for key, value in values.items() if key.startswith("HERMES_LANGFUSE_")
        }
        assert bool(langfuse_values) is expected
        if expected:
            assert langfuse_values == {
                "HERMES_LANGFUSE_PUBLIC_KEY": public_key,
                "HERMES_LANGFUSE_SECRET_KEY": secret_key,
                "HERMES_LANGFUSE_BASE_URL": "https://langfuse.example.test",
                "HERMES_LANGFUSE_CAPTURE": "sanitized",
                "HERMES_LANGFUSE_ENV": "production",
            }


def test_root_and_profile_templates_share_the_gated_partials() -> None:
    includes = {
        "config.yaml.j2": "config-langfuse-observability.yaml.j2",
        "config-profile.yaml.j2": "config-langfuse-observability.yaml.j2",
        "hermes-env.j2": "hermes-env-langfuse.j2",
        "hermes-env-profile.j2": "hermes-env-langfuse.j2",
    }
    for template, partial in includes.items():
        source = (TEMPLATES / template).read_text()
        assert f"{{% include '{partial}' %}}" in source


def test_credentials_are_read_from_the_agent_openbao_view() -> None:
    source = (REPO_ROOT / "inventory/group_vars/hermes_agent_group/00-agent-settings.yml").read_text()
    assert "hermes_agent_bao.LANGFUSE_PUBLIC_KEY" in source
    assert "hermes_agent_bao.LANGFUSE_SECRET_KEY" in source
    assert "lookup('env', 'LANGFUSE_" not in source


def test_managed_sdk_pin_matches_the_supported_plugin_imports() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_langfuse_sdk_version"] == "4.15.2"
    tasks = (ROLE_ROOT / "tasks/venv_extras_and_users.yml").read_text()
    assert "langfuse{{ hermes_agent_langfuse_sdk_pin }}" in tasks
    assert "from langfuse import Langfuse, propagate_attributes" in tasks


def test_retired_otel_plugin_and_environment_are_absent() -> None:
    checked_roots = (
        ROLE_ROOT / "defaults",
        ROLE_ROOT / "tasks",
        TEMPLATES,
        REPO_ROOT / "inventory/group_vars/hermes_agent_group",
        REPO_ROOT / "tests/hermes_agent",
    )
    sources = "\n".join(
        path.read_text()
        for root in checked_roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".j2", ".yml", ".yaml", ".py"}
    )
    retired_plugin = "observability/" + "otel"
    retired_env_prefix = "HERMES_" + "OTEL_"
    assert retired_plugin not in sources
    assert retired_env_prefix not in sources
