"""Self-check that profile-homed Hermes cron jobs never receive Slack tokens.

Runs bare (`python3 tests/hermes_agent/test_alert_routing_profile_tokens.py`) or under pytest.
"""

from __future__ import annotations

import re

from jinja2 import Environment
import yaml

from _alert_routing_shared import DEFAULTS
from test_profile_contract import (
    _RENDER_CONTEXT as _PROFILE_CONFIG_CONTEXT,
    _defaults as _profile_defaults,
    _jinja_env as _profile_jinja_env,
    _profiles,
)
from test_profile_env_parity import _context as _profile_env_context
from test_profile_env_parity import _env_values as _profile_env_values

def _profile_homed_cron_jobs() -> dict[tuple[str, str], dict]:
    """Find every cron record with its own named-profile HERMES_HOME."""
    jobs: dict[tuple[str, str], dict] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if {"name", "schedule", "hermes_home"} <= value.keys():
                jobs[(str(value["name"]), str(value["hermes_home"]))] = value
                return
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for value in DEFAULTS.values():
        visit(value)
    return jobs


def test_profile_homed_crons_never_receive_slack_tokens() -> None:
    jobs = _profile_homed_cron_jobs()
    assert jobs, "role defaults contain no profile-homed cron jobs"
    profiles = {profile["name"]: profile for profile in _profiles()}
    env = _profile_jinja_env()
    profile_env_context = _profile_env_context()
    defaults = _profile_defaults()
    profile_config_context = dict(_PROFILE_CONFIG_CONTEXT)
    profile_config_context.update(
        hermes_agent_langfuse_enabled=True,
        hermes_agent_plugins_enabled=["dryvist-guardrails", "hindsight", "langfuse"],
        hermes_agent_disabled_toolsets=defaults["hermes_agent_disabled_toolsets"],
        hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_context_limits={},
    )
    profile_config = env.get_template("config-profile.yaml.j2")

    for cron_name, home_template in jobs:
        home = Environment().from_string(home_template).render(
            hermes_agent_hermes_home="/hermes-home",
            hermes_agent_kanban_orchestrator_profile="orchestrator",
            hermes_agent_credential_canary_profile=(
                DEFAULTS["hermes_agent_credential_canary_profile"]
            ),
        )
        match = re.fullmatch(r"/hermes-home/profiles/([^/]+)", home)
        assert match, f"profile-homed cron {cron_name} has unexpected home {home!r}"
        profile_name = match.group(1)
        assert profile_name in profiles, f"{cron_name} names unknown profile {profile_name!r}"
        profile = profiles[profile_name]

        granted_token_keys = [
            key for key in profile.get("env", []) if re.fullmatch(r"SLACK_.*TOKEN", str(key))
        ]
        assert not granted_token_keys, (
            f"profile {profile_name!r}, used by {cron_name}, grants Slack token keys "
            f"{granted_token_keys}"
        )

        rendered_env = env.get_template("hermes-env-profile.j2").render(
            hermes_agent_profile=profile, **profile_env_context
        )
        profile_values = _profile_env_values(rendered_env)
        leaked_tokens = {
            key: value
            for key, value in profile_values.items()
            if re.fullmatch(r"SLACK_.*TOKEN", key) and value
        }
        assert not leaked_tokens, (
            f"profile {profile_name!r}, used by {cron_name}, renders Slack tokens "
            f"{sorted(leaked_tokens)}"
        )

        rendered_config = profile_config.render(
            hermes_agent_profile=profile, **profile_config_context
        )
        parsed_config = yaml.safe_load(rendered_config)
        assert not re.search(r"\bSLACK_.*TOKEN\b", yaml.safe_dump(parsed_config)), (
            f"profile {profile_name!r}, used by {cron_name}, config names a Slack token"
        )


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
