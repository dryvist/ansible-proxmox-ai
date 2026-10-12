"""Roster identities project their identity and Slack channels into role defaults."""

from __future__ import annotations

import re

from jinja2 import Environment

from _role_files import role_defaults
from conftest import ROLE_ROOT


_DEFAULT_KEYS = (
    "hermes_agent_id",
    "hermes_agent_env_prefix",
    "hermes_agent_display_name",
    "hermes_agent_user",
    "hermes_agent_home",
    "hermes_agent_slack_home_channel",
)


def _render_identity_defaults(**overrides: object) -> dict[str, str]:
    defaults = role_defaults(ROLE_ROOT)
    context: dict[str, object] = {"hermes_agent_identity": "hermes"}
    context.update(overrides)
    env = Environment(autoescape=False)
    env.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
        pattern, replacement, value
    )
    env.globals["lookup"] = lambda _plugin, _name: ""

    rendered: dict[str, str] = {}
    for key in _DEFAULT_KEYS:
        rendered[key] = env.from_string(str(defaults[key])).render(
            **context, **rendered
        )
    return rendered


def test_legacy_identity_keeps_existing_user_home_and_display() -> None:
    rendered = _render_identity_defaults()

    assert rendered["hermes_agent_user"] == "hermes"
    assert rendered["hermes_agent_home"] == "/var/lib/hermes"
    assert rendered["hermes_agent_display_name"] == "Hermes"


def test_roster_identity_projects_display_user_home_and_home_channel() -> None:
    rendered = _render_identity_defaults(
        hermes_agent_identity="splunk-heimdall",
        ai_agents={
            "splunk-heimdall": {
                "display": "Heimdall",
                "slack": {
                    "home_channel": "example-home-channel",
                    "requests_channel": "example-requests-channel",
                },
            }
        },
    )

    assert rendered["hermes_agent_user"] == "splunk-heimdall"
    assert rendered["hermes_agent_home"] == "/var/lib/splunk-heimdall"
    assert rendered["hermes_agent_display_name"] == "Heimdall"
    assert rendered["hermes_agent_slack_home_channel"] == "example-home-channel"
