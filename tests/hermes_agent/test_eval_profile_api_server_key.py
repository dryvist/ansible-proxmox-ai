from __future__ import annotations

from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _defaults, _jinja_env, _profiles
from _role_files import role_tasks


EVAL_API_SERVER_KEY = "eval-api-server-fixture"
DEFAULT_API_SERVER_KEY = "default-api-server-fixture"


def _env_values(rendered: str) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in rendered.splitlines()
        if "=" in line and not line.startswith("#")
    )


def _resolve_eval_api_server_key(secrets: dict[str, str]) -> str:
    default = _defaults()["hermes_agent_eval_api_server_key"]
    return _jinja_env().from_string(str(default)).render(bao_apps_secrets=secrets)


def _render_profile_env(profile: dict, eval_api_server_key: str) -> str:
    context = dict(_RENDER_CONTEXT)
    profile_key = f"hermes_{profile['name'].replace('-', '_')}_llm_router_key"
    context.update(
        bao_apps_secrets={profile_key: "router-key-fixture"},
        hermes_agent_eval_api_server_key=eval_api_server_key,
        hermes_agent_api_server_key=DEFAULT_API_SERVER_KEY,
        hermes_agent_owned_mcp_slugs=[],
        hermes_agent_splunk_access=True,
        hermes_agent_splunk_mcp_url="splunk-url-fixture",
        hermes_agent_splunk_mcp_token="splunk-token-fixture",
        hermes_agent_slack_bot_token="slack-bot-fixture",
        hermes_agent_slack_app_token="slack-app-fixture",
        hermes_agent_slack_allowed_users="slack-users-fixture",
        hermes_agent_slack_home_channel="slack-channel-fixture",
        hermes_agent_slack_home_channel_name="slack-name-fixture",
        hermes_agent_zammad_url="zammad-url-fixture",
        hermes_agent_zammad_api_token="zammad-token-fixture",
        hermes_agent_github_read_token="github-token-fixture",
        hermes_agent_homeassistant_enabled=True,
        hermes_agent_homeassistant_token="homeassistant-token-fixture",
        hermes_agent_homeassistant_url="homeassistant-url-fixture",
        hermes_agent_homeassistant_home_channel="homeassistant-channel-fixture",
        hermes_agent_homeassistant_entity_denylist="homeassistant-entities-fixture",
    )
    return _jinja_env().get_template("hermes-env-profile.j2").render(
        hermes_agent_profile=profile, **context
    )


def test_only_eval_profile_renders_its_own_openbao_api_server_key() -> None:
    resolved = _resolve_eval_api_server_key(
        {"hermes_eval_api_server_key": EVAL_API_SERVER_KEY}
    )
    assert resolved == EVAL_API_SERVER_KEY

    for profile in _profiles():
        rendered = _render_profile_env(profile, resolved)
        values = _env_values(rendered)
        if profile["name"] == "eval":
            assert values["API_SERVER_KEY"] == EVAL_API_SERVER_KEY
            assert values["API_SERVER_KEY"] != DEFAULT_API_SERVER_KEY
        else:
            assert "API_SERVER_KEY" not in values, profile["name"]
            assert EVAL_API_SERVER_KEY not in rendered, profile["name"]


def test_eval_profile_omits_an_empty_api_server_key() -> None:
    assert _resolve_eval_api_server_key({}) == ""
    profile = next(profile for profile in _profiles() if profile["name"] == "eval")

    rendered = _render_profile_env(profile, "")

    assert "API_SERVER_KEY" not in _env_values(rendered)


def test_eval_profile_keeps_ungranted_integration_credentials_blank() -> None:
    profile = next(profile for profile in _profiles() if profile["name"] == "eval")
    rendered = _render_profile_env(profile, EVAL_API_SERVER_KEY)
    values = _env_values(rendered)
    forbidden_keys = {
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SLACK_ALLOWED_USERS",
        "SLACK_HOME_CHANNEL",
        "SLACK_HOME_CHANNEL_NAME",
        "GH_PAT_WRITE_PROJECT_ISSUES",
        "HERMES_AGENT_GATEWAY_KEY",
        "SPLUNK_MCP_URL",
        "SPLUNK_MCP_TOKEN",
        "ZAMMAD_URL",
        "ZAMMAD_API_TOKEN",
        "HASS_TOKEN",
        "HASS_URL",
        "HASS_HOME_CHANNEL",
        "HASS_ENTITY_DENYLIST",
    }

    assert profile["mcp"] == []
    assert profile["memory_enabled"] is False
    assert not {key: values.get(key) for key in forbidden_keys if values.get(key)}


def test_profile_env_deploy_task_is_no_log() -> None:
    task = next(
        task for task in role_tasks(ROLE_ROOT)
        if task.get("name") == "Deploy profile secrets (.env) for {{ item.name }}"
    )

    assert task["no_log"] is True
