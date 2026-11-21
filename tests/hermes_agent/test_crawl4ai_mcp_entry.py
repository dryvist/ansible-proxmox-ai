"""The crawl4ai MCP client entry: off by default, switched by its own flag,
dialed through the gateway /crawl4ai route like the task-tracker entry —
the owning agent presents its gateway key, the gateway holds the upstream
token."""

from __future__ import annotations

from typing import Any

import yaml
from test_owned_mcp_servers import _config_context, _mcp_servers
from test_profile_contract import _RENDER_CONTEXT, _defaults, _jinja_env

URL = "https://mcp.example.com/crawl4ai"
TIMEOUT = _RENDER_CONTEXT["hermes_agent_mcp_tool_timeout_seconds"]
PROFILE = {"name": "page-scout", "description": "fixture", "mcp": ["crawl4ai"], "env": []}


def _config(**overrides: Any) -> dict[str, Any]:
    """The full config.yaml.j2 render context the owned-MCP tests use, plus crawl4ai overrides."""
    return {**_config_context("example-agent"), **overrides}


def _profile_context(**overrides: Any) -> dict[str, Any]:
    # The profile template needs the same keys test_profile_contract's scoped-
    # render test supplies, on top of the shared config context.
    defaults = _defaults()
    context = _config(**overrides)
    context.update(
        hermes_agent_langfuse_enabled=True,
        hermes_agent_plugins_enabled=["example-guardrails", "hindsight", "langfuse"],
        hermes_agent_disabled_toolsets=defaults["hermes_agent_disabled_toolsets"],
        hermes_agent_cron_toolsets=defaults["hermes_agent_api_server_toolsets"],
        hermes_agent_context_limits={},
    )
    return context


def _render_config(context: dict[str, Any]) -> str:
    # trim_blocks=True: the template module renders with trim_blocks on, so a
    # disabled block must emit zero bytes (no stray blank lines).
    env = _jinja_env()
    env.trim_blocks = True
    return env.get_template("config.yaml.j2").render(**context)


def _render_profile(profile: dict[str, Any], context: dict[str, Any]) -> str:
    env = _jinja_env()
    env.trim_blocks = True
    return env.get_template("config-profile.yaml.j2").render(
        hermes_agent_profile=profile, **context
    )


def test_entry_defaults_to_disabled_with_the_gateway_vhost_url() -> None:
    defaults = _defaults()

    assert defaults["hermes_agent_crawl4ai_mcp_enabled"] is False
    rendered_url = (
        _jinja_env()
        .from_string(defaults["hermes_agent_crawl4ai_mcp_url"])
        .render(
            lookup=lambda kind, name: "example-subdomain"
            if name == "PROXMOX_SUBDOMAIN"
            else "",
            undef=lambda message: (_ for _ in ()).throw(ValueError(message)),
        )
    )
    assert rendered_url == "https://mcp.example-subdomain/crawl4ai"


def test_defaults_render_is_unchanged_with_the_entry_disabled() -> None:
    # With the flag off, every block this feature adds emits nothing under
    # trim_blocks=True (the template module's setting), and every line the
    # blocks COULD emit contains the server name — so its absence from the
    # render is the byte-for-byte proof that defaults changed nothing.
    config = _config()
    profile = _profile_context()

    assert "crawl4ai" not in _render_config(config).lower()
    assert "crawl4ai" not in _render_profile(PROFILE, profile).lower()


def test_enabled_entry_without_ownership_sends_no_caller_key() -> None:
    # example-agent's roster (see _config_context) owns no crawl4ai slug, so
    # the entry presents no X-Agent-Key — the gateway would reject a key it
    # has not allowlisted for this caller.
    context = _config(
        hermes_agent_crawl4ai_mcp_enabled=True,
        hermes_agent_crawl4ai_mcp_url=URL,
    )
    servers = _mcp_servers(_render_config(context))

    assert servers["crawl4ai"] == {"timeout": TIMEOUT, "url": URL}
    assert "headers" not in servers["crawl4ai"]


def test_owner_entry_sends_the_caller_key_in_config_and_profile() -> None:
    expected = {
        "timeout": TIMEOUT,
        "url": URL,
        "headers": {"X-Agent-Key": "${HERMES_AGENT_GATEWAY_KEY}"},
    }

    config = _config(
        hermes_agent_crawl4ai_mcp_enabled=True,
        hermes_agent_crawl4ai_mcp_url=URL,
        hermes_agent_owned_mcp_slugs=["crawl4ai"],
    )
    assert _mcp_servers(_render_config(config))["crawl4ai"] == expected

    profile = _profile_context(
        hermes_agent_crawl4ai_mcp_enabled=True,
        hermes_agent_crawl4ai_mcp_url=URL,
        hermes_agent_owned_mcp_slugs=["crawl4ai"],
    )
    granted = yaml.safe_load(_render_profile(PROFILE, profile))["mcp_servers"]
    assert granted["crawl4ai"] == expected


def test_profile_entry_needs_the_server_in_the_profile_mcp_list_and_the_flag() -> None:
    enabled = _profile_context(
        hermes_agent_crawl4ai_mcp_enabled=True,
        hermes_agent_crawl4ai_mcp_url=URL,
    )
    granted = yaml.safe_load(_render_profile(PROFILE, enabled))["mcp_servers"]
    assert granted["crawl4ai"] == {"timeout": TIMEOUT, "url": URL}

    unflagged = _profile_context(hermes_agent_crawl4ai_mcp_url=URL)
    disabled = yaml.safe_load(_render_profile(PROFILE, unflagged)).get("mcp_servers", {})
    assert "crawl4ai" not in disabled

    unlisted = dict(PROFILE, mcp=["docs"])
    ungranted = yaml.safe_load(_render_profile(unlisted, enabled)).get("mcp_servers", {})
    assert "crawl4ai" not in ungranted
