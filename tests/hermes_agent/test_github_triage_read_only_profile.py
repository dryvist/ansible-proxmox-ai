from __future__ import annotations

from pathlib import Path

from test_profile_contract import _defaults, _profiles

ROOT = Path(__file__).resolve().parents[2]
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


def test_github_triage_copy_on_the_default_home_is_withdrawn_when_the_job_moves() -> None:
    # The direct-cron reconcile withdraws only from the home a job declares, so a
    # copy left on the default home needs the removal list below to reach it.
    assert GITHUB_TRIAGE_NAME in _defaults()["hermes_agent_moved_off_default_home_cron_names"]

    drift = (ROOT / "roles/hermes_agent/tasks/ssh_and_cron_drift.yml").read_text()
    assert "hermes_agent_moved_off_default_home_cron_names" in drift
