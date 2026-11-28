from __future__ import annotations

import re

from _shell_harness_shared import defaults_of


GATE = "llm_router_hermes_vikunja_clerk_variants_enabled"
NEW_PROFILES = {
    "vikunja-clerk-workstation": ("vikunja_workstation", "vikunja-clerk.soul.md"),
    "vikunja-clerk-hermes": ("vikunja_hermes", "vikunja-clerk.soul.md"),
    "vikunja-clerk-pepper": ("vikunja_pepper", "vikunja-clerk.soul.md"),
    "vikunja-clerk-untrusted": ("vikunja_untrusted", "vikunja-clerk.soul.md"),
    "vikunja-clerk-jobs": ("vikunja_jobs", "vikunja-clerk.soul.md"),
    "vikunja-clerk-inbox": ("vikunja_inbox", "vikunja-clerk.soul.md"),
    "vikunja-triage": ("vikunja_triage", "vikunja-triage.soul.md"),
    "vikunja-nightly": ("vikunja_nightly", "vikunja-nightly.soul.md"),
    "vikunja-windows": ("vikunja_windows", "vikunja-windows.soul.md"),
    "vikunja-clerk-acceptance": ("vikunja_acceptance", "vikunja-clerk.soul.md"),
    "vikunja-triage-acceptance": ("vikunja_acceptance", "vikunja-triage.soul.md"),
    "vikunja-nightly-acceptance": ("vikunja_acceptance", "vikunja-nightly.soul.md"),
    "vikunja-windows-acceptance": ("vikunja_acceptance", "vikunja-windows.soul.md"),
}
# Trusted tracker profiles keep conventions in a bank: the clerk variants share one, the others own
# one. The untrusted, inbox and acceptance profiles have none.
BANKS = {
    "vikunja-clerk-workstation": "vikunja-clerk",
    "vikunja-clerk-hermes": "vikunja-clerk",
    "vikunja-clerk-pepper": "vikunja-clerk",
    "vikunja-clerk-jobs": "vikunja-clerk",
    "vikunja-triage": "vikunja-triage",
    "vikunja-nightly": "vikunja-nightly",
    "vikunja-windows": "vikunja-windows",
}
LOCKED_KEYS = {
    "name",
    "description",
    "mcp",
    "locked",
    "feature_gate",
    "soul_standalone_file",
}


def _defaults() -> dict:
    return defaults_of("hermes_agent")


def _profiles_by_name() -> dict[str, dict]:
    return {
        profile["name"]: profile
        for profile in _defaults()["hermes_agent_profiles"]
    }


def test_thirteen_new_profiles_exist_once_and_keep_the_original_clerk() -> None:
    profiles = _defaults()["hermes_agent_profiles"]
    for name in NEW_PROFILES:
        assert sum(profile["name"] == name for profile in profiles) == 1

    original = [profile for profile in profiles if profile["name"] == "vikunja-clerk"]
    assert len(original) == 1
    assert original[0]["mcp"] == ["vikunja_clerk"]


def test_new_profiles_are_locked_minimal_single_server_rows() -> None:
    profiles = _profiles_by_name()
    for name, (mcp_name, soul) in NEW_PROFILES.items():
        profile = profiles[name]
        bank = BANKS.get(name)
        assert set(profile) == LOCKED_KEYS | ({"memory_bank_id"} if bank else set())
        if bank:
            assert profile["memory_bank_id"] == "{{ hermes_agent_memory_bank_id ~ '-%s' }}" % bank
        assert profile["locked"] is True
        assert profile["feature_gate"] == GATE
        assert profile["mcp"] == [mcp_name]
        assert len(profile["mcp"]) == 1
        assert profile["soul_standalone_file"] == soul


def test_new_profiles_are_dropped_when_their_gate_is_false_or_undefined() -> None:
    defaults = _defaults()
    profiles = defaults["hermes_agent_profiles"]
    visible = {
        profile["name"]
        for profile in profiles
        if not profile.get("feature_gate")
        or defaults.get(profile["feature_gate"], False)
    }
    assert set(NEW_PROFILES).isdisjoint(visible)


def test_new_profile_names_and_descriptions_are_safe() -> None:
    profiles = _profiles_by_name()
    host_pattern = re.compile(
        r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\b", re.IGNORECASE
    )
    ipv4_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    for name in NEW_PROFILES:
        profile = profiles[name]
        assert re.fullmatch(r"[a-z0-9-]+", name)
        assert not host_pattern.search(profile["description"])
        assert not ipv4_pattern.search(profile["description"])
