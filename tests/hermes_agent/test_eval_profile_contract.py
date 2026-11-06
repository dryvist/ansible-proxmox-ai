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
    context["hermes_agent_auxiliary_tasks"] = defaults["hermes_agent_auxiliary_tasks"]
    src = (ROLE_ROOT / "templates" / "config-profile.yaml.j2").read_text()
    return yaml.safe_load(_jinja_env().from_string(src).render(hermes_agent_profile=profile, **context))


def test_eval_completion_and_auxiliary_models_stay_within_the_key_grant() -> None:
    # Use the registry's local review alias, without retyping a serving name.
    repo = ROLE_ROOT.parents[1]
    registry = yaml.safe_load((repo / "llm-models.d/10-large.yml").read_text())
    model = next(
        alias
        for entry in registry["_llm_registry_large"]
        for alias in entry.get("stable_aliases", [])
        if alias.startswith("review-") and alias.endswith("local")
    )
    env = _jinja_env()
    resolved = env.from_string(EVAL_PROFILE["model"]).render(
        llm_router_review_aliases={"local": model}
    )
    assert resolved == model
    profile = {**EVAL_PROFILE, "model": resolved}
    parsed = _render_config(profile, "hindsight")
    assert parsed["model"]["default"] == model
    assert parsed["compression"]["summary_model"] == model
    routes = parsed["auxiliary"]
    assert set(routes) == set(_defaults()["hermes_agent_auxiliary_tasks"]) | {
        "goal_judge", "transient_retries"
    }
    assert all(route["model"] == model for name, route in routes.items() if name != "transient_retries")
    assert "mcp_servers" not in parsed
    assert parsed["memory"]["memory_enabled"] is False


def test_eval_model_guard_rejects_a_non_permitted_model() -> None:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/profiles.yml").read_text())
    guard = next(task for task in tasks if task["name"] == "Refuse an eval model outside its router key grant")
    env = _jinja_env()
    condition = env.compile_expression(guard["ansible.builtin.assert"]["that"][0])
    context = dict(hermes_agent_model="fleet-model", llm_router_review_aliases={"local": "local-fixture"})
    assert condition(item={"model": "local-fixture"}, **context)
    assert not condition(item={"model": "non-permitted-fixture"}, **context)
    assert not condition(item={}, **context)


def test_eval_profile_grants_only_its_api_server_env_and_no_mcp_or_skill() -> None:
    assert EVAL_PROFILE["env"] == ["api_server"]
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
