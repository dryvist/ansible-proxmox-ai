from __future__ import annotations

from typing import Any

import yaml
from test_profile_contract import (
    _RENDER_CONTEXT,
    ROLE_ROOT,
    _defaults,
    _jinja_env,
    _profiles,
)

EVAL_PROFILE = next(profile for profile in _profiles() if profile["name"] == "eval")
PRIVILEGED_TOOLSETS = {
    "terminal",
    "code_execution",
    "memory",
    "kanban",
    "browser",
    "delegation",
    "cronjob",
    "clarify",
}


def _render_config(profile: dict[str, Any], memory_provider: str) -> dict[str, Any]:
    defaults = _defaults()
    context = dict(_RENDER_CONTEXT)
    context["hermes_agent_memory_provider"] = memory_provider
    context["hermes_agent_langfuse_enabled"] = True
    context["hermes_agent_plugins_enabled"] = ["dryvist-guardrails", "hindsight", "langfuse"]
    context["hermes_agent_disabled_toolsets"] = defaults["hermes_agent_disabled_toolsets"]
    context["hermes_agent_cron_toolsets"] = defaults["hermes_agent_api_server_toolsets"]
    context["hermes_agent_context_limits"] = {}
    src = (ROLE_ROOT / "templates" / "config-profile.yaml.j2").read_text()
    return yaml.safe_load(_jinja_env().from_string(src).render(hermes_agent_profile=profile, **context))


def test_eval_profile_grants_no_env_mcp_or_skill() -> None:
    assert EVAL_PROFILE["env"] == []
    assert EVAL_PROFILE["mcp"] == []
    assert EVAL_PROFILE["skills"] == []
    assert "soul_addendum_bundle" not in EVAL_PROFILE


def test_eval_profile_has_no_cron_job_and_no_board_assignment() -> None:
    for entry in _defaults()["hermes_agent_direct_cron_jobs"]:
        assert not str(entry.get("hermes_home", "")).endswith("/profiles/eval"), entry["name"]
    assert EVAL_PROFILE["toolsets"]["cron"] == []


def test_eval_toolsets_are_api_server_minus_terminal_code_execution_file_and_memory() -> None:
    api_server = set(_defaults()["hermes_agent_api_server_toolsets"])
    cli = set(EVAL_PROFILE["toolsets"]["cli"])

    assert cli == api_server - {"terminal", "code_execution", "file", "memory"}
    assert not cli & PRIVILEGED_TOOLSETS


def test_eval_memory_stays_off_whatever_the_fleet_provider_is() -> None:
    for provider in ("hindsight", "builtin"):
        parsed = _render_config(EVAL_PROFILE, provider)

        assert parsed["memory"]["provider"] == "builtin"
        assert parsed["memory"]["memory_enabled"] is False
        assert parsed["memory"]["user_profile_enabled"] is False


def test_other_profiles_keep_fleet_memory_on() -> None:
    splunk = next(profile for profile in _profiles() if profile["name"] == "splunk-admin")
    parsed = _render_config(splunk, "hindsight")

    assert parsed["memory"]["provider"] == "hindsight"
    assert parsed["memory"]["memory_enabled"] is True
    assert parsed["memory"]["user_profile_enabled"] is True
