"""The Home Assistant plugin is presence-gated and reaches homelab-admin alone, through its config, toolset, env and install home."""

from __future__ import annotations

import ast
from typing import Any

import yaml

from _role_files import role_defaults
from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _jinja_env, _profiles

PROFILE = "homelab-admin"


def _defaults() -> dict[str, Any]:
    return role_defaults(ROLE_ROOT)


def _core_names() -> list[str]:
    return [plugin["name"] for plugin in _defaults()["hermes_agent_plugins_core"]]


def _gate(token: str, url: str) -> bool:
    expression = _defaults()["hermes_agent_homeassistant_enabled"]
    rendered = _jinja_env().from_string(str(expression)).render(
        hermes_agent_homeassistant_token=token,
        hermes_agent_homeassistant_url=url,
    )
    return rendered.strip() == "True"


def _open(name: str, gate: bool) -> list[str]:
    expression = _defaults()[name]
    rendered = _jinja_env().from_string(str(expression)).render(hermes_agent_homeassistant_enabled=gate)
    return ast.literal_eval(rendered.strip())


def _profile_context(gate: bool) -> dict[str, Any]:
    context = dict(_RENDER_CONTEXT)
    context.update(
        hermes_agent_langfuse_enabled=False,
        hermes_agent_plugins_enabled=_core_names(),
        hermes_agent_profile_plugins_open=_open("hermes_agent_profile_plugins_open", gate),
        hermes_agent_profile_toolsets_open=_open("hermes_agent_profile_toolsets_open", gate),
        hermes_agent_disabled_toolsets=_defaults()["hermes_agent_disabled_toolsets"],
        hermes_agent_cron_toolsets=_defaults()["hermes_agent_api_server_toolsets"],
        hermes_agent_context_limits={},
    )
    return context


def test_gate_needs_both_the_token_and_the_url() -> None:
    assert _gate("HATOK", "https://ha.example.com") is True
    assert _gate("", "https://ha.example.com") is False
    assert _gate("HATOK", "") is False
    assert _gate("", "") is False


def test_only_homelab_admin_enables_homeassistant_and_only_while_the_gate_is_open() -> None:
    partial = _jinja_env().get_template("config-plugins.yaml.j2")
    for gate in (False, True):
        open_plugins = _open("hermes_agent_profile_plugins_open", gate)
        root = yaml.safe_load(
            partial.render(hermes_agent_plugins_enabled=_core_names(), hermes_agent_profile_plugins_open=open_plugins)
        )
        assert "homeassistant" not in root["plugins"]["enabled"]
        for profile in _profiles():
            rendered = partial.render(
                hermes_agent_plugins_enabled=_core_names(),
                hermes_agent_profile=profile,
                hermes_agent_profile_plugins_open=open_plugins,
            )
            enabled = yaml.safe_load(rendered)["plugins"]["enabled"]
            expected = gate and profile["name"] == PROFILE
            assert ("homeassistant" in enabled) is expected, (profile["name"], gate)


def test_only_homelab_admin_lifts_the_homeassistant_toolset_and_only_while_the_gate_is_open() -> None:
    template = _jinja_env().get_template("config-profile.yaml.j2")
    global_denials = _defaults()["hermes_agent_disabled_toolsets"]
    for gate in (False, True):
        for profile in _profiles():
            parsed = yaml.safe_load(template.render(hermes_agent_profile=profile, **_profile_context(gate)))
            lifted = gate and profile["name"] == PROFILE
            expected = [name for name in global_denials if not (lifted and name == "homeassistant")]
            assert parsed["agent"]["disabled_toolsets"] == expected, (profile["name"], gate)


def test_hass_env_is_rendered_for_homelab_admin_only_while_the_gate_is_open() -> None:
    template = _jinja_env().get_template("hermes-env-profile.j2")
    for gate in (False, True):
        context = dict(_RENDER_CONTEXT)
        context.update(
            hermes_agent_model_api_key="MODELKEY",
            hermes_agent_homeassistant_enabled=gate,
            hermes_agent_homeassistant_token="HATOK",
            hermes_agent_homeassistant_url="https://ha.example.com",
            hermes_agent_homeassistant_home_channel="notify_target",
            hermes_agent_homeassistant_entity_denylist="lock.*",
        )
        for profile in _profiles():
            name = profile["name"]
            context["bao_apps_secrets"] = {f"hermes_{name.replace('-', '_')}_llm_router_key": "MODELKEY"}
            rendered = template.render(hermes_agent_profile=profile, **context)
            values = dict(
                line.split("=", 1) for line in rendered.splitlines() if "=" in line and not line.startswith("#")
            )
            on = gate and name == PROFILE
            expected = {
                "HASS_TOKEN": "HATOK" if on else "",
                "HASS_URL": "https://ha.example.com" if on else "",
                "HASS_HOME_CHANNEL": "notify_target" if on else "",
                "HASS_ENTITY_DENYLIST": "lock.*" if on else "",
            }
            for key, value in expected.items():
                assert values[key] == value, (name, gate, key)


def test_plugin_installs_into_the_homelab_admin_home_only() -> None:
    expression = _defaults()["hermes_agent_homeassistant_plugin_homes"]
    for gate in (False, True):
        rendered = _jinja_env().from_string(str(expression)).render(
            hermes_agent_homeassistant_enabled=gate,
            hermes_agent_hermes_home="/var/lib/hermes",
        )
        homes = ast.literal_eval(rendered.strip())
        assert homes == (["/var/lib/hermes/profiles/homelab-admin"] if gate else [])


def test_isolation_stays_in_process_in_the_rendered_plugin_config() -> None:
    partial = _jinja_env().get_template("config-plugins.yaml.j2")
    root = yaml.safe_load(partial.render(hermes_agent_plugins_enabled=_core_names()))
    assert "isolation" not in root["plugins"]
    for profile in _profiles():
        rendered = partial.render(
            hermes_agent_plugins_enabled=_core_names(), hermes_agent_profile=profile
        )
        assert "isolation" not in yaml.safe_load(rendered)["plugins"], profile["name"]


def test_homeassistant_plugin_is_pinned_to_the_reviewed_commit() -> None:
    (plugin,) = _defaults()["hermes_agent_plugins_homeassistant"]
    assert plugin["name"] == "homeassistant"
    assert plugin["archive_url"].endswith("/4d492b1dc5326af3e38bb2cf3e4b9ad6d65f0072")
    assert plugin["archive_subdir"].endswith("/homeassistant")
    assert len(plugin["archive_sha256"]) == 64
    assert all("==" in requirement for requirement in plugin["requirements"])
