"""Each Hermes store uses ONLY its own per-profile router key.

roles/llm_router A4 (56-virtual-keys.yml) seeds one virtual key per Hermes
profile — hermes_<profile>_llm_router_key, mount `apps`, read here as
bao_apps_secrets — so a store's spend attributes to that profile.

Operator rule (2026-09-21): nothing that calls the router holds the shared
master key. A store whose own key is not yet seeded must fail the render
loudly (mandatory) rather than silently widen onto the master key or onto a
sibling store's key.
"""

from __future__ import annotations

import pytest
from jinja2 import Environment, FileSystemLoader, Undefined

from conftest import ROLE_ROOT


def _mandatory(value, msg=""):
    if isinstance(value, Undefined):
        raise ValueError(msg)
    return value


def _jinja_env() -> Environment:
    env = Environment(autoescape=False, loader=FileSystemLoader(ROLE_ROOT / "templates"))
    env.filters["comment"] = lambda v: f"# {v}"
    env.filters["bool"] = bool
    env.filters["mandatory"] = _mandatory
    return env


def _model_api_key(rendered: str) -> str:
    line = next(line for line in rendered.splitlines() if line.startswith("HERMES_AGENT_MODEL_API_KEY="))
    return line.split("=", 1)[1]


def _base_context() -> dict:
    # Every other var the two templates dereference unconditionally, so a
    # render under test doesn't fail on an unrelated undefined value. None of
    # these are exercised by the assertions below.
    return dict(
        ansible_managed="managed",
        # hermes_agent_model_api_key is itself the default store's mandatory
        # scoped key now (defaults/main/20-brain-and-slack.yml) — never the
        # master key. hermes-env.j2 just relays it.
        hermes_agent_model_api_key="sk-default-own-key",
        hermes_agent_memory_provider="hindsight",
        hermes_agent_memory_mode="local_external",
        hermes_agent_wiki_enabled=False,
        hermes_agent_firecrawl_enabled=False,
        hermes_agent_webhook_enabled=False,
        hermes_agent_api_server_key="",
        hermes_agent_slack_bot_token="",
        hermes_agent_slack_app_token="",
        hermes_agent_slack_allowed_users="",
        hermes_agent_slack_home_channel="",
        hermes_agent_slack_home_channel_name="",
        hermes_agent_splunk_mcp_url="",
        hermes_agent_splunk_mcp_token="",
        hermes_agent_zammad_url="",
        hermes_agent_zammad_api_token="",
        hermes_agent_github_read_token="",
        hermes_agent_chromium_executable="/usr/bin/chromium",
        hermes_agent_browser_args="",
    )


def test_default_store_uses_its_own_seeded_key() -> None:
    env = _jinja_env()
    context = _base_context()
    context["bao_apps_secrets"] = {"hermes_default_llm_router_key": "sk-default-own-key"}
    rendered = env.get_template("hermes-env.j2").render(**context)
    assert _model_api_key(rendered) == "sk-default-own-key"


def test_named_profile_uses_its_own_seeded_key() -> None:
    env = _jinja_env()
    context = _base_context()
    context["hermes_agent_profile"] = {"name": "splunk-admin", "env": []}
    context["bao_apps_secrets"] = {
        "hermes_splunk_admin_llm_router_key": "sk-splunk-admin-own-key",
        # A sibling profile's key must never leak into this one's render.
        "hermes_homelab_admin_llm_router_key": "sk-homelab-admin-own-key",
    }
    rendered = env.get_template("hermes-env-profile.j2").render(**context)
    assert _model_api_key(rendered) == "sk-splunk-admin-own-key"


def test_named_profile_never_falls_back_to_a_sibling_or_the_default_key() -> None:
    """A profile without its own seeded key fails loudly — it must not
    silently run on the default store's key (which is itself never the
    master key any more, but is still a broader identity than this profile
    declared)."""
    env = _jinja_env()
    context = _base_context()
    context["hermes_agent_profile"] = {"name": "github-maint", "env": []}
    # A DIFFERENT profile's key is present, but github-maint's own is not.
    context["bao_apps_secrets"] = {"hermes_splunk_admin_llm_router_key": "sk-splunk-admin-own-key"}
    with pytest.raises(ValueError):
        env.get_template("hermes-env-profile.j2").render(**context)


def test_rendered_env_never_carries_the_shared_master_key_variable() -> None:
    """The master key never appears anywhere in a rendered store's .env —
    only each store's own seeded value, sourced from bao_apps_secrets."""
    env = _jinja_env()
    context = _base_context()
    context["bao_apps_secrets"] = {"hermes_default_llm_router_key": "sk-default-own-key"}
    rendered = env.get_template("hermes-env.j2").render(**context)
    assert "ai_orchestration_model_api_key" not in rendered
    assert "SHARED-MASTER-KEY" not in rendered
