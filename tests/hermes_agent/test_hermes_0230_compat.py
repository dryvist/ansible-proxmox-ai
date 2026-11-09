from __future__ import annotations

from _role_files import role_defaults
from test_context_limits import ROLE_ROOT, _render


def test_hermes_0230_release_pins_match_the_tagged_runtime() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_version"] == "0.23.0"
    assert defaults["hermes_agent_version_tag"] == "v{{ hermes_agent_version }}"
    assert defaults["hermes_agent_installer_sha256"] == (
        "abf338605dae6dd28d101673ac6aed7995c955745db46d47676bc591a7957209"
    )
    assert defaults["hermes_agent_slack_bolt_version"] == "1.30.0"
    assert defaults["hermes_agent_slack_sdk_version"] == "3.45.0"
    assert defaults["hermes_agent_slack_aiohttp_version"] == "3.14.5"
    assert defaults["hermes_agent_otlp_sdk_version"] == "1.45.1"
    assert defaults["hermes_agent_firecrawl_py_version"] == "4.53.2"


def test_gateway_and_profiles_render_hermes_023_browser_config() -> None:
    expected = {
        "headed": False,
        "inactivity_timeout": 120,
        "executable_path": "/usr/bin/chromium",
        "chromium_args": ["--no-sandbox"],
    }
    for template in ("config.yaml.j2", "config-profile.yaml.j2"):
        rendered, config = _render(template, {})
        assert config["browser"] == expected, template
        assert config["security"]["allow_private_urls"] is True, template
        assert "record_sessions" not in rendered
        assert "allow_private_urls" not in config["browser"]
        assert "backend" not in config["browser"]


def test_pepper_keeps_browser_access_denied_after_the_browser_tool_change() -> None:
    _, config = _render("config.yaml.j2", {}, hermes_agent_id="pepper")
    assert "browser" in config["agent"]["disabled_toolsets"]


def test_browser_settings_are_configured_in_yaml_not_legacy_environment() -> None:
    role = ROLE_ROOT
    for template in ("hermes-env.j2", "hermes-env-profile.j2"):
        rendered = (role / "templates" / template).read_text()
        assert "AGENT_BROWSER_" not in rendered
        assert "BROWSER_INACTIVITY_TIMEOUT" not in rendered
