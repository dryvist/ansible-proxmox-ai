"""Each Hermes store selects its OWN per-profile router key.

roles/llm_router A4 (56-virtual-keys.yml) seeds one virtual key per Hermes
profile — hermes_<profile>_llm_router_key, mount `apps`, read here as
bao_apps_secrets — so a store's spend attributes to that profile instead of
every store chaining off the one shared HERMES_AGENT_MODEL_API_KEY.

Before its own key is seeded, a store must keep working: fall back to the
shared key rather than render an empty credential.
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from conftest import ROLE_ROOT


def _jinja_env() -> Environment:
    env = Environment(autoescape=False, loader=FileSystemLoader(ROLE_ROOT / "templates"))
    env.filters["comment"] = lambda v: f"# {v}"
    env.filters["bool"] = bool
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
        hermes_agent_model_api_key="SHARED-MASTER-KEY",
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


def test_default_store_falls_back_when_its_key_is_not_seeded() -> None:
    env = _jinja_env()
    context = _base_context()
    context["bao_apps_secrets"] = {}
    rendered = env.get_template("hermes-env.j2").render(**context)
    assert _model_api_key(rendered) == "SHARED-MASTER-KEY"


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


def test_named_profile_falls_back_when_its_key_is_not_seeded() -> None:
    env = _jinja_env()
    context = _base_context()
    context["hermes_agent_profile"] = {"name": "github-maint", "env": []}
    # A DIFFERENT profile's key is present, but github-maint's own is not.
    context["bao_apps_secrets"] = {"hermes_splunk_admin_llm_router_key": "sk-splunk-admin-own-key"}
    rendered = env.get_template("hermes-env-profile.j2").render(**context)
    assert _model_api_key(rendered) == "SHARED-MASTER-KEY"
