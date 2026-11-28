"""Maintenance-only tool access for tracker memory banks."""
from __future__ import annotations

import yaml

from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _defaults, _jinja_env


def _render_profile(
    profile: dict,
    manifest_by_soul: dict | None = None,
    *,
    omit_tracker_defaults: bool = False,
    **extra,
) -> str:
    defaults = _defaults()
    if omit_tracker_defaults:
        defaults.pop("hermes_agent_tracker_bank_tools_enabled", None)
        defaults.pop("hermes_agent_tracker_bank_tool_names", None)
    context = {
        **defaults,
        **_RENDER_CONTEXT,
        "hermes_agent_model_base_url": "router-base/v1",
        "hermes_agent_profile": profile,
        "hermes_agent_profile_prompt_manifest_by_soul": manifest_by_soul or {},
        "hermes_agent_disabled_toolsets": defaults["hermes_agent_disabled_toolsets"],
        "hermes_agent_cron_toolsets": defaults["hermes_agent_api_server_toolsets"],
        "hermes_agent_context_limits": {},
        "hermes_agent_auxiliary_tasks": defaults["hermes_agent_auxiliary_tasks"],
        "hermes_agent_kanban_goal_mode": False,
        "hermes_agent_crawl4ai_mcp_enabled": False,
        "hermes_agent_router_zammad_mcp_enabled": True,
        "hermes_agent_langfuse_enabled": False,
        "hermes_agent_hindsight_config_api_url": "memory-base/",
        "hermes_agent_hindsight_config_api_key": "test-key-123",
    }
    context.update(extra)
    return _jinja_env().get_template("config-profile.yaml.j2").render(**context)


def _render_profile_env(profile: dict, tracker_tools_enabled: bool | None) -> str:
    context = dict(_RENDER_CONTEXT)
    profile_key = f"hermes_{profile['name'].replace('-', '_')}_llm_router_key"
    profile_secrets = {profile_key: "router-key-fixture"}
    if profile.get("locked", False):
        profile_secrets[
            f"hermes_{profile['name'].replace('-', '_')}_api_server_key"
        ] = "api-server-key-fixture"
    context.update(
        bao_apps_secrets=profile_secrets,
        hermes_agent_hindsight_config_api_key="test-key-123",
        hermes_agent_owned_mcp_slugs=[],
        hermes_agent_splunk_access=True,
        hermes_agent_splunk_mcp_url="mcp-base",
        hermes_agent_splunk_mcp_token="splunk-token-fixture",
        hermes_agent_slack_bot_token="slack-bot-fixture",
        hermes_agent_slack_app_token="slack-app-fixture",
        hermes_agent_slack_allowed_users="slack-users-fixture",
        hermes_agent_slack_home_channel="slack-channel-fixture",
        hermes_agent_slack_home_channel_name="slack-name-fixture",
        hermes_agent_zammad_url="mcp-base",
        hermes_agent_zammad_api_token="zammad-token-fixture",
        hermes_agent_github_read_token="github-token-fixture",
        hermes_agent_homeassistant_enabled=True,
        hermes_agent_homeassistant_token="homeassistant-token-fixture",
        hermes_agent_homeassistant_url="mcp-base",
        hermes_agent_homeassistant_home_channel="homeassistant-channel-fixture",
        hermes_agent_homeassistant_entity_denylist="homeassistant-entities-fixture",
    )
    if tracker_tools_enabled is not None:
        context["hermes_agent_tracker_bank_tools_enabled"] = tracker_tools_enabled
    return _jinja_env().get_template("hermes-env-profile.j2").render(
        hermes_agent_profile=profile, **context
    )


def _tracker_profile(memory_bank_id: str | None = "bank-x") -> dict:
    profile = {
        "name": "tracker-fixture",
        "description": "fixture",
        "locked": True,
        "mcp": ["tracker_fixture"],
        "soul_standalone_file": "tracker-fixture.soul.md",
    }
    if memory_bank_id is not None:
        profile["memory_bank_id"] = memory_bank_id
    return profile


def test_switch_off_leaves_config_unchanged_and_omits_memory_server() -> None:
    profile = _tracker_profile()
    rendered = _render_profile(profile, hermes_agent_tracker_bank_tools_enabled=False)
    without_new_defaults = _render_profile(profile, omit_tracker_defaults=True)

    assert rendered == without_new_defaults
    assert "memory_bank" not in yaml.safe_load(rendered).get("mcp_servers", {})


def test_enabled_bank_server_uses_scoped_url_tools_and_key_reference() -> None:
    rendered = _render_profile(
        _tracker_profile(), hermes_agent_tracker_bank_tools_enabled=True
    )
    config = yaml.safe_load(rendered)
    server = config["mcp_servers"]["memory_bank"]

    assert server["url"] == "memory-base/mcp/bank-x/"
    assert server["tools"]["include"] == [
        "list_memories",
        "get_memory",
        "invalidate_memory",
        "list_documents",
        "delete_document",
    ]
    assert server["headers"]["Authorization"] == "Bearer ${MEMORY_BANK_API_KEY}"
    assert "test-key-123" not in rendered


def test_enabled_platform_lists_allow_tracker_server_but_not_bank_tools() -> None:
    profiles = (
        _tracker_profile(),
        {
            **_tracker_profile(),
            "toolsets": {
                "api_server": ["keep-api"],
                "cron": ["keep-cron"],
                "cli": ["keep-cli"],
            },
        },
        {**_tracker_profile(), "toolsets": {"cli": ["keep-cli"]}},
    )
    for profile in profiles:
        toolsets = yaml.safe_load(
            _render_profile(profile, hermes_agent_tracker_bank_tools_enabled=True)
        )["platform_toolsets"]

        for platform in ("api_server", "cron"):
            assert isinstance(toolsets[platform], list)
            assert "memory_bank" not in toolsets[platform]
            assert "mcp-memory_bank" not in toolsets[platform]
            assert "mcp-tracker_fixture" in toolsets[platform]
        if "api_server" in profile.get("toolsets", {}):
            assert "keep-api" in toolsets["api_server"]
        if "cron" in profile.get("toolsets", {}):
            assert "keep-cron" in toolsets["cron"]


def test_enabled_profile_without_bank_omits_memory_server() -> None:
    config = yaml.safe_load(
        _render_profile(
            _tracker_profile(memory_bank_id=None),
            hermes_agent_tracker_bank_tools_enabled=True,
        )
    )

    assert "memory_bank" not in config.get("mcp_servers", {})


def test_locked_disabled_toolsets_never_denylist_memory_bank_aliases() -> None:
    config = yaml.safe_load(
        _render_profile(_tracker_profile(), hermes_agent_tracker_bank_tools_enabled=True)
    )

    disabled = config["agent"].get("disabled_toolsets", [])
    assert "memory_bank" not in disabled
    assert "mcp-memory_bank" not in disabled


def test_tracker_bank_defaults_share_the_five_maintenance_tools_once() -> None:
    defaults = _defaults()
    names = defaults["hermes_agent_tracker_bank_tool_names"]
    expected = [
        "list_memories",
        "get_memory",
        "invalidate_memory",
        "list_documents",
        "delete_document",
    ]
    source = (ROLE_ROOT / "defaults/main/70-memory.yml").read_text(encoding="utf-8")

    assert names == expected
    assert all(source.count(name) == 1 for name in expected)
    for bank in defaults["hermes_agent_tracker_memory_banks"]:
        tools = _jinja_env().from_string(bank["updates"]["mcp_enabled_tools"])
        on = tools.render(
            hermes_agent_tracker_bank_tool_names=names, hermes_agent_tracker_bank_tools_enabled=True
        )
        off = tools.render(
            hermes_agent_tracker_bank_tool_names=names, hermes_agent_tracker_bank_tools_enabled=False
        )
        assert yaml.safe_load(on) == ["recall", "retain", *expected]
        assert yaml.safe_load(off) == ["recall", "retain"]


def test_enabled_env_and_config_use_key_value_only_through_env_reference() -> None:
    profile = _tracker_profile()
    env = _render_profile_env(profile, tracker_tools_enabled=True)
    config = _render_profile(
        profile,
        hermes_agent_tracker_bank_tools_enabled=True,
        hermes_agent_hindsight_config_api_key="test-key-123",
    )

    assert "MEMORY_BANK_API_KEY=test-key-123" in env.splitlines()
    assert 'Bearer ${MEMORY_BANK_API_KEY}' in config
    assert "test-key-123" not in config


def test_enabled_env_blanks_key_for_locked_profile_without_bank() -> None:
    env = _render_profile_env(
        _tracker_profile(memory_bank_id=None), tracker_tools_enabled=True
    )

    assert "MEMORY_BANK_API_KEY=" in env.splitlines()


def test_disabled_env_is_unchanged_and_omits_memory_bank_key() -> None:
    profile = _tracker_profile()
    without_switch = _render_profile_env(profile, tracker_tools_enabled=None)
    disabled = _render_profile_env(profile, tracker_tools_enabled=False)

    assert disabled == without_switch
    assert "MEMORY_BANK_API_KEY" not in disabled
