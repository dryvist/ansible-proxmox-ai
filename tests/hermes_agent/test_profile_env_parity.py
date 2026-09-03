"""Operational settings must reach every Hermes store, not just the default.

A named profile's scheduled work runs as `hermes cron tick` under that
profile's own HERMES_HOME, and the CLI loads only the .env belonging to that
home (dotenv, override=True) before any agent code runs. A setting written to
the default store alone therefore does not apply to a profile's jobs at all —
it is not merely overridden, it is absent, and nothing about the default store
looks wrong when that happens.

The structural fix is one shared partial (templates/hermes-env-operational.j2)
included by both env templates. This module is the guard on top of it: it
renders the real templates and compares the resulting key/value sets, so it
also fails for a NEW setting added to the default template alone — which the
include cannot prevent on its own.
"""

from __future__ import annotations

import json
from typing import Any

from jinja2 import Environment, FileSystemLoader

from conftest import ROLE_ROOT
from _role_files import role_defaults


# Keys the default store renders that no profile store carries. Each is a
# deliberate exclusion, and the parity check below turns anything NOT listed
# here into a failure — so adding a key to hermes-env.j2 forces a choice: put
# it in the shared operational partial, where every store gets it, or record it
# here as default-store-only. Making that choice explicit is the whole point:
# left unmade, a setting reaches the default store's jobs and no profile's.
_DEFAULT_STORE_ONLY_KEYS = {
    # Inbound platforms, served by the long-running gateway process only.
    "WEBHOOK_ENABLED",
    "WEBHOOK_PORT",
    "WEBHOOK_SECRET",
    "API_SERVER_ENABLED",
    "API_SERVER_KEY",
    "API_SERVER_HOST",
    "API_SERVER_PORT",
    # Slack routing ids, read by the gateway's own emitters.
    "SLACK_FIREHOSE_CHANNEL",
    "SLACK_HERMES_ALL_CHANNEL",
    "SLACK_HERMES_ISSUES_CHANNEL",
    "SLACK_HERMES_NOISE_CHANNEL",
    "SLACK_HERMES_SPLUNK_CHANNEL",
    # OTLP export, emitted by the gateway process.
    "HERMES_OTEL_ENABLED",
    "HERMES_OTEL_ENDPOINT",
    "HERMES_OTEL_TIMEOUT",
    "OTEL_SERVICE_NAME",
    # Local-embedded memory daemon: one per gateway, not one per profile.
    "HINDSIGHT_LLM_API_KEY",
    # The signed-commit App key path and the Browser Use CLI wiring — the
    # profile template deliberately carries neither.
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "BROWSER_INACTIVITY_TIMEOUT",
    "BROWSER_USE_HOME",
    "BH_HOME",
    "BH_CHROME_PATH",
    "BU_CDP_URL",
}


def _jinja_env() -> Environment:
    # A loader, so `{% include %}` resolves out of the role's templates/ dir
    # the way Ansible's own template action resolves it.
    env = Environment(autoescape=False, loader=FileSystemLoader(ROLE_ROOT / "templates"))
    env.filters["to_json"] = json.dumps
    env.filters["bool"] = bool
    env.filters["comment"] = lambda v: f"# {v}"
    return env


def _env_values(rendered: str) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in rendered.splitlines()
        if "=" in line and not line.startswith("#")
    )


def _context() -> dict[str, Any]:
    """Role defaults with every conditional gate forced open.

    Both templates render against the SAME context, so a difference between the
    two stores can only come from the templates themselves. Every `{% if %}` is
    opened so a key added inside one is covered too — a guard that saw only the
    unconditional blocks would miss half the file.
    """
    context: dict[str, Any] = dict(role_defaults(ROLE_ROOT))
    context.update(
        ansible_managed="managed",
        hermes_agent_model_api_key="MODELKEY",
        hermes_agent_memory_provider="hindsight",
        hermes_agent_memory_mode="local_embedded",
        hermes_agent_wiki_enabled=True,
        hermes_agent_wiki_path="/var/lib/hermes/wiki",
        hermes_agent_firecrawl_enabled=True,
        hermes_agent_webhook_enabled=True,
        hermes_agent_webhook_secret="WEBHOOKSECRET",
        hermes_agent_api_server_key="APIKEY",
        hermes_agent_slack_bot_token="xoxb-x",
        hermes_agent_slack_app_token="xapp-x",
        hermes_agent_splunk_mcp_token="SPLUNKTOK",
        hermes_agent_zammad_api_token="ZAMTOK",
        hermes_agent_github_read_token="READTOK",
        hermes_agent_github_issues_pat="WRITETOK",
    )
    return context


def test_every_operational_setting_reaches_every_profile_store() -> None:
    env = _jinja_env()
    context = _context()
    shared = _env_values(env.get_template("hermes-env-operational.j2").render(**context))
    default_values = _env_values(env.get_template("hermes-env.j2").render(**context))
    assert shared, "the shared operational partial rendered no settings at all"

    for profile in role_defaults(ROLE_ROOT)["hermes_agent_profiles"]:
        profile_values = _env_values(
            env.get_template("hermes-env-profile.j2").render(
                hermes_agent_profile=profile, **context
            )
        )

        for key, value in shared.items():
            assert default_values.get(key) == value, (
                f"{key} is missing from the default store, or differs from the "
                "shared operational partial"
            )
            assert profile_values.get(key) == value, (
                f"profile {profile['name']!r}: {key} must render identically to "
                f"the default store ({value!r}), got {profile_values.get(key)!r}"
            )

        unshared = set(default_values) - set(profile_values) - _DEFAULT_STORE_ONLY_KEYS
        assert not unshared, (
            f"profile {profile['name']!r} never sees {sorted(unshared)}, which the "
            "default store sets. Move the setting into "
            "templates/hermes-env-operational.j2 so every store gets it, or add it "
            "to _DEFAULT_STORE_ONLY_KEYS to record that the default store keeps it."
        )
