"""Only the default store holds the Slack credential.

Hermes parks a profile whose platform token another profile also holds and
keeps the gateway standalone, which stops that profile's cron jobs and Kanban
workers ("Gateway inaccessible: profile_parked"). A named profile granted the
`slack` env section renders the shared bot/app token, so none may take it.
"""

from __future__ import annotations

from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _jinja_env, _profiles


def test_no_named_profile_takes_the_slack_section() -> None:
    holders = [p["name"] for p in _profiles() if "slack" in (p.get("env") or [])]
    assert holders == [], f"profiles {holders} would share the default store's Slack token"


def test_named_profile_env_renders_no_slack_token() -> None:
    src = (ROLE_ROOT / "templates" / "hermes-env-profile.j2").read_text()
    template = _jinja_env().from_string(src)
    for profile in _profiles():
        context = dict(_RENDER_CONTEXT)
        context.update(
            hermes_agent_profile=profile,
            hermes_agent_model_api_key="MODELKEY",
            bao_apps_secrets={},
            hermes_agent_slack_bot_token="xoxb-x",
            hermes_agent_slack_app_token="xapp-x",
        )
        rendered = template.render(**context)
        assert "xoxb-x" not in rendered and "xapp-x" not in rendered, profile["name"]


def test_named_profile_env_renders_an_empty_app_token_line() -> None:
    """The blank literal is the enforcement, not a stylistic choice.

    A profile worker inherits the gateway process's full environment and loads
    this file with dotenv override=True: an absent SLACK_APP_TOKEN line would
    leave the main gateway's Socket Mode token readable in the profile, and a
    non-empty one would open a second socket on the same Slack app (the
    runtime refuses the duplicate claim and disables Slack on it). One Slack
    socket per identity: every profile renders the line present and empty.
    """
    src = (ROLE_ROOT / "templates" / "hermes-env-profile.j2").read_text()
    template = _jinja_env().from_string(src)
    for profile in _profiles():
        context = dict(_RENDER_CONTEXT)
        context.update(
            hermes_agent_profile=profile,
            hermes_agent_model_api_key="MODELKEY",
            bao_apps_secrets={},
            hermes_agent_slack_bot_token="xoxb-x",
            hermes_agent_slack_app_token="xapp-x",
        )
        rendered = template.render(**context)
        assert "\nSLACK_APP_TOKEN=\n" in rendered, profile["name"]
        assert "\nSLACK_BOT_TOKEN=\n" in rendered, profile["name"]
