from __future__ import annotations

from test_profile_contract import _defaults, _profiles

GITHUB_TRIAGE_NAME = "{{ hermes_agent_github_monitor_cron_name }}"
READ_ONLY_PROFILE_HOME = "{{ hermes_agent_hermes_home }}/profiles/github-maint"


def _github_triage() -> dict:
    return next(
        entry
        for entry in _defaults()["hermes_agent_direct_cron_jobs"]
        if entry["name"] == GITHUB_TRIAGE_NAME
    )


def test_github_triage_runs_on_the_read_only_github_maint_profile() -> None:
    entry = _github_triage()
    profile = next(p for p in _profiles() if p["name"] == "github-maint")

    assert entry["hermes_home"] == READ_ONLY_PROFILE_HOME
    assert profile["env"] == ["github"]


def test_github_triage_is_gated_on_the_read_only_token_not_the_write_identity() -> None:
    entry = _github_triage()

    assert entry["skill"] == "dryvist/github-issues"
    assert "hermes_agent_github_read_token" in entry["enabled"]
    assert "hermes_agent_github_identity_enabled" not in entry["enabled"]
