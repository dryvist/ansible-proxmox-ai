"""Locked profile facts, router tool servers, and prompt manifest tool lists."""
from __future__ import annotations

from pathlib import Path

import yaml

from _shell_harness_shared import render
from test_profile_contract import _RENDER_CONTEXT, ROLE_ROOT, _defaults, _jinja_env


REPO_ROOT = Path(__file__).resolve().parents[2]


def _mcp_fragment(profile: dict, manifest_entry: dict) -> dict:
    source = (REPO_ROOT / "roles/hermes_agent/templates/config-profile.yaml.j2").read_text(
        encoding="utf-8"
    )
    start = source.index("{% set _mcp_splunk")
    end = source.index("\ntimezone:", start)
    fragment = source[start:end]
    context = {
        "hermes_agent_profile": profile,
        # The fragment starts below the template line that sets this flag.
        "_profile_locked": bool(profile.get("locked", False)),
        "hermes_agent_profile_prompt_manifest_by_soul": {
            profile["soul_standalone_file"]: manifest_entry,
        },
        "hermes_agent_splunk_access": False,
        "hermes_agent_splunk_mcp_enabled": False,
        "hermes_agent_docs_mcp_enabled": False,
        "hermes_agent_grep_mcp_enabled": False,
        "hermes_agent_vikunja_mcp_enabled": False,
        "hermes_agent_crawl4ai_mcp_enabled": False,
        "hermes_agent_router_zammad_mcp_enabled": False,
        "hermes_agent_nautobot_mcp_enabled": False,
        "hermes_agent_owned_mcp_slugs": [],
        "hermes_agent_mcp_tool_timeout_seconds": 10,
        "hermes_agent_model_base_url": "fixture-router/v1",
    }
    rendered = render(fragment, context)
    return yaml.safe_load(rendered) or {}


def _render_profile(profile: dict, manifest_by_soul: dict | None = None, **extra: object) -> str:
    defaults = _defaults()
    context = {
        **defaults,
        **_RENDER_CONTEXT,
        "hermes_agent_model_base_url": "https://router.example.invalid/v1",
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
        **extra,
    }
    return _jinja_env().get_template("config-profile.yaml.j2").render(**context)


def test_profile_tool_list_uses_manifest_and_preserves_no_field_behavior() -> None:
    profile = {
        "name": "vikunja-clerk-workstation",
        "description": "fixture",
        "locked": True,
        "mcp": ["vikunja_workstation"],
        "soul_standalone_file": "vikunja-clerk.soul.md",
    }
    with_tools = _mcp_fragment(
        profile,
        {"tools": ["read_task"], "write_tools": ["create_task"]},
    )
    server = with_tools["mcp_servers"]["vikunja_workstation"]
    assert server["tools"]["include"] == ["read_task", "create_task"]

    without_tools = _mcp_fragment(profile, {"sha256": "fixture-digest"})
    server = without_tools["mcp_servers"]["vikunja_workstation"]
    assert "tools" not in server


def test_locked_profile_role_facts_render_as_one_system_prompt_string() -> None:
    profile = {
        "name": "vikunja-clerk-workstation",
        "description": "fixture",
        "locked": True,
        "mcp": ["vikunja_workstation"],
        "role_facts": "You act as the workstation role.",
    }
    rendered = yaml.safe_load(_render_profile(profile))
    assert rendered["agent"]["system_prompt"] == profile["role_facts"]

    without_facts = dict(profile)
    without_facts.pop("role_facts")
    rendered = yaml.safe_load(_render_profile(without_facts))
    assert "system_prompt" not in rendered["agent"]


def test_role_facts_come_from_the_run_time_map_by_profile_name() -> None:
    profile = {
        "name": "vikunja-triage",
        "description": "fixture",
        "locked": True,
        "mcp": ["vikunja_triage"],
    }
    facts = {"vikunja-triage": "Your inbox project is Fixture inbox.", "other": "Not yours."}
    rendered = yaml.safe_load(_render_profile(profile, hermes_agent_profile_role_facts=facts))
    assert rendered["agent"]["system_prompt"] == facts["vikunja-triage"]

    own = dict(profile, role_facts="The row's own facts.")
    rendered = yaml.safe_load(_render_profile(own, hermes_agent_profile_role_facts=facts))
    assert rendered["agent"]["system_prompt"] == "The row's own facts."

    rendered = yaml.safe_load(_render_profile(dict(profile, locked=False), hermes_agent_profile_role_facts=facts))
    assert "system_prompt" not in rendered["agent"]


def test_locked_profiles_always_get_their_router_server() -> None:
    locked = {
        "name": "vikunja-clerk-workstation",
        "description": "fixture",
        "locked": True,
        "mcp": ["vikunja_workstation"],
        "soul_standalone_file": "vikunja-clerk.soul.md",
    }
    locked_servers = _mcp_fragment(locked, {})["mcp_servers"]
    assert locked_servers["vikunja_workstation"]["headers"]["x-mcp-servers"] == "vikunja_workstation"

    unlocked = dict(locked, locked=False)
    unlocked_servers = _mcp_fragment(unlocked, {}).get("mcp_servers", {})
    assert "vikunja_workstation" not in unlocked_servers


def test_locked_defaults_without_manifest_tools_render_no_prompt_or_include() -> None:
    locked_profiles = [p for p in _defaults()["hermes_agent_profiles"] if p.get("locked")]
    assert locked_profiles
    for profile in locked_profiles:
        rendered = _render_profile(profile, {})
        assert "system_prompt:" not in rendered
        assert "include:" not in rendered


def test_locked_profile_guard_allows_role_facts() -> None:
    guard = yaml.safe_load((ROLE_ROOT / "tasks/assert-locked-profile.yml").read_text())
    allowed_keys = guard[0]["ansible.builtin.assert"]["that"]
    difference = next(rule for rule in allowed_keys if "difference(" in rule)
    assert "'role_facts'" in difference
