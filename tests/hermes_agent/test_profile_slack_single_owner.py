"""Only the default (main-gateway) store holds the Slack credential pair.

One Slack socket per identity: the identity's main gateway holds both Slack
tokens; a named profile holds none and its cron output is delivered through
the primary gateway's profile_routes (upstream parks a gateway holding the
same Slack token as another — gateway/run_adapters.py
_refuse_duplicate_claim — and serves a satellite through the primary's
routes precisely because its home never holds platform credentials,
cron/scheduler_preflight.py _primary_profile_routes_for_current_home). No
profile may take the `slack` env section, and the profile env template
renders every Slack line blank even if a future defaults change granted one.
"""

from __future__ import annotations

from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _jinja_env, _profiles


def test_no_named_profile_takes_the_slack_section() -> None:
    holders = [p["name"] for p in _profiles() if "slack" in (p.get("env") or [])]
    assert holders == [], f"profiles {holders} would share the default store's Slack token"


def _render_profile_env(profile: dict) -> str:
    template = _jinja_env().from_string(
        (ROLE_ROOT / "templates" / "hermes-env-profile.j2").read_text()
    )
    context = dict(_RENDER_CONTEXT)
    context.update(
        hermes_agent_profile=profile,
        hermes_agent_model_api_key="MODELKEY",
        bao_apps_secrets={
            f"hermes_{profile['name'].replace('-', '_')}_llm_router_key": "MODELKEY"
        },
        hermes_agent_slack_bot_token="xoxb-x",
        hermes_agent_slack_app_token="xapp-x",
    )
    return template.render(**context)


def test_named_profile_env_renders_no_slack_token() -> None:
    for profile in _profiles():
        rendered = _render_profile_env(profile)
        assert "xoxb-x" not in rendered and "xapp-x" not in rendered, profile["name"]


def test_even_a_slack_granted_profile_renders_an_empty_app_token() -> None:
    """The token rule is unconditional in the template, not gated on the grant.

    A hypothetical future defaults change granting `slack` to a profile must
    still render SLACK_BOT_TOKEN= and SLACK_APP_TOKEN= blank — the one-socket
    rule is structural, not a property of today's profile list.
    """
    rendered = _render_profile_env(
        {
            "name": "slack-fixture",
            "description": "hypothetical slack-granted profile",
            "env": ["slack"],
            "mcp": [],
            "skills": [],
        }
    )
    values = dict(
        line.split("=", 1)
        for line in rendered.splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert values["SLACK_APP_TOKEN"] == ""
    assert values["SLACK_BOT_TOKEN"] == ""
    assert "xapp-x" not in rendered and "xoxb-x" not in rendered
