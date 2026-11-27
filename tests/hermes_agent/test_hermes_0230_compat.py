from __future__ import annotations

from _role_files import role_defaults
from test_context_limits import ROLE_ROOT, _render


# Literal version pins are not asserted here: Renovate owns them, and the
# installer-checksum workflow checks the installer hash against the pinned tag.
def test_hermes_release_tag_derives_from_the_pinned_version() -> None:
    defaults = role_defaults(ROLE_ROOT)
    assert defaults["hermes_agent_version_tag"] == "v{{ hermes_agent_version }}"


def test_gateway_and_profiles_render_hermes_023_browser_config() -> None:
    expected = {
        "headed": False,
        "inactivity_timeout": 120,
        "auto_local_for_private_urls": True,
        "executable_path": "/usr/bin/chromium",
        "chromium_args": ["--no-sandbox"],
    }
    for template in ("config.yaml.j2", "config-profile.yaml.j2"):
        rendered, config = _render(template, {})
        assert config["browser"] == expected, template
        assert "record_sessions" not in rendered
        assert "allow_private_urls" not in config["browser"]
        assert "backend" not in config["browser"]


def test_named_profile_does_not_enable_private_url_fetch_bypass() -> None:
    _, config = _render("config-profile.yaml.j2", {})
    assert config["security"]["allow_private_urls"] is False
    assert "allow_private_urls" not in config["browser"]


def test_gateway_keeps_private_browser_routing_without_private_fetch_bypass() -> None:
    _, config = _render("config.yaml.j2", {})
    assert config["security"]["allow_private_urls"] is False
    assert config["browser"]["auto_local_for_private_urls"] is True
    assert "allow_private_urls" not in config["browser"]


def test_pepper_keeps_browser_access_denied_after_the_browser_tool_change() -> None:
    _, config = _render("config.yaml.j2", {}, hermes_agent_id="pepper")
    assert "browser" in config["agent"]["disabled_toolsets"]


def test_browser_settings_are_configured_in_yaml_not_legacy_environment() -> None:
    role = ROLE_ROOT
    for template in ("hermes-env.j2", "hermes-env-profile.j2"):
        rendered = (role / "templates" / template).read_text()
        assert "AGENT_BROWSER_" not in rendered
        assert "BROWSER_INACTIVITY_TIMEOUT" not in rendered
