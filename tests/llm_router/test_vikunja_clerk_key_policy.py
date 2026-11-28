"""Caller policy for the per-role tracker clerk variants."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "hermes_agent"))
from _shell_harness_shared import defaults_of, render  # noqa: E402

ROUTER = defaults_of("llm_router")
CALLING_ROLES = ["workstation", "hermes", "pepper", "untrusted", "jobs", "inbox"]
AGENTS = ["clerk", "triage", "nightly", "windows"]


def _policy() -> dict:
    value = ROUTER["llm_router_vikunja_clerk_key_policy"]
    if isinstance(value, str):
        value = yaml.safe_load(render(value, dict(ROUTER), lenient=True))
    return value


def test_switch_off_by_default_and_default_alias_is_inbox() -> None:
    policy = _policy()
    assert policy["enabled"] is False
    assert policy["default_aliases"] == {"agent-vikunja-clerk": "agent-vikunja-clerk-inbox"}


def test_outside_trusted_keys_get_only_the_untrusted_variant() -> None:
    entry = _policy()["outside_trusted"]
    assert entry["aliases"] == {"agent-vikunja-clerk": "agent-vikunja-clerk-untrusted"}
    assert entry["models"] == ["agent-vikunja-clerk-untrusted"]


def test_each_role_reaches_only_its_own_clerk_variant() -> None:
    policy = _policy()
    variants = {f"agent-vikunja-clerk-{role}" for role in CALLING_ROLES}
    for role in CALLING_ROLES:
        entry = policy["roles"][role]
        target = f"agent-vikunja-clerk-{role}"
        assert entry["aliases"] == {"agent-vikunja-clerk": target}
        assert set(entry["models"]) & variants == {target}
    for role in ("pepper", "untrusted", "inbox"):
        assert policy["roles"][role]["models"] == [f"agent-vikunja-clerk-{role}"]


def test_youtrack_clerk_is_workstation_only_and_schedules_belong_to_hermes() -> None:
    roles = _policy()["roles"]
    assert "agent-youtrack-clerk" in roles["workstation"]["models"]
    assert all("agent-youtrack-clerk" not in roles[r]["models"] for r in CALLING_ROLES if r != "workstation")
    assert {"agent-vikunja-triage", "agent-vikunja-nightly", "agent-vikunja-windows"} <= set(roles["hermes"]["models"])
    assert "agent-vikunja-triage" not in roles["workstation"]["models"]
    assert "agent-vikunja-windows" in roles["workstation"]["models"]
    assert "agent-vikunja-windows" in roles["jobs"]["models"]


def test_key_roles_fail_closed_for_the_shared_proxy_and_exclude_ci() -> None:
    policy = _policy()
    key_roles = policy["key_roles"]
    assert key_roles["litellm-local-workstation"] == "inbox"
    assert key_roles["hermes-default"] == "hermes"
    assert key_roles["claude-code"] == "workstation"
    assert set(key_roles.values()) <= set(CALLING_ROLES)
    excluded = set(policy["excluded_key_aliases"])
    assert {"github-actions", "github-actions-ci-fix", "github-actions-pr-review",
            "github-actions-oss", "openhands"} <= excluded
    assert excluded.isdisjoint(key_roles)
    assert "evals" not in key_roles


def test_evaluation_key_reaches_only_acceptance_variants() -> None:
    acceptance = _policy()["acceptance"]
    assert acceptance["key_alias"] == "evals"
    assert acceptance["aliases"] == {
        f"agent-vikunja-{agent}": f"agent-vikunja-{agent}-acceptance" for agent in AGENTS
    }
    assert set(acceptance["models"]) == set(acceptance["aliases"].values())


def test_key_scopes_consume_the_policy_and_template_honours_the_gate() -> None:
    scopes = (REPO_ROOT / "roles/llm_router/defaults/main/59-key-scopes.yml").read_text(encoding="utf-8")
    assert "llm_router_vikunja_clerk_key_policy" in scopes
    assert "excluded_key_aliases" in scopes
    template = (REPO_ROOT / "roles/llm_router/templates/model-list-hermes-agents.yaml.j2").read_text(encoding="utf-8")
    assert "feature_gate" in template


def test_enabled_scope_projection_applies_trust_before_key_roles() -> None:
    policy = _policy()
    policy["enabled"] = True
    locked_models = [
        model
        for entry in policy["roles"].values()
        for model in entry["models"]
    ] + policy["acceptance"]["models"] + list(policy["acceptance"]["aliases"])
    keys = [
        {"alias": "claude-code", "trust_caller": "fixture-workstation", "models": []},
        {"alias": "codex", "trust_caller": "fixture-outside", "models": []},
        {"alias": "agy", "trust_caller": "fixture-inbox", "models": []},
    ]
    context = {
        **ROUTER,
        "_llm_router_hermes_profile_keys": [],
        "_llm_router_static_virtual_keys": keys,
        "_llm_router_target_virtual_keys": [],
        "_llm_router_workstation_virtual_keys": [],
        "_llm_router_agent_keys": [],
        "_llm_router_hermes_locked_profile_models": locked_models,
        "llm_router_hermes_clerk_caller_key_aliases": ["claude-code", "codex", "agy"],
        "llm_router_vikunja_clerk_key_policy": policy,
        "_llm_router_trusted_caller_tiers": ["trusted"],
        "agent_callers_default_tier": "untrusted",
        "ai_callers": {
            "fixture-workstation": {"trust_tier": "trusted"},
            "fixture-outside": {"trust_tier": "public"},
            "fixture-inbox": {"trust_tier": "trusted"},
        },
    }
    projected = yaml.safe_load(render(ROUTER["_llm_router_declared_virtual_keys"], context))
    by_alias = {key["alias"]: key for key in projected}

    assert set(by_alias["claude-code"]["models"]) == set(policy["roles"]["workstation"]["models"])
    assert by_alias["claude-code"]["aliases"] == policy["roles"]["workstation"]["aliases"]
    assert by_alias["codex"]["models"] == policy["outside_trusted"]["models"]
    assert by_alias["codex"]["aliases"] == policy["outside_trusted"]["aliases"]
    assert by_alias["agy"]["models"] == policy["roles"]["inbox"]["models"]
    assert by_alias["agy"]["aliases"] == {}
