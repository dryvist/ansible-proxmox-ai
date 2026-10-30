"""Only jobs that require an available Hindsight service pause in containment."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = REPO_ROOT / "roles/hermes_agent/defaults/main"
ENV = jinja2.Environment()
ENV.filters["bool"] = lambda value: str(value).lower() in {"true", "1", "yes", "on"}


def _enabled(expression: str, service_enabled: bool) -> bool:
    context = {
        "hindsight_service_enabled": service_enabled,
        "hermes_agent_service_pulse_enabled": True,
        "hermes_agent_splunk_health_enabled": True,
        "hermes_agent_llm_stats_hourly_enabled": service_enabled,
        "hermes_agent_self_audit_enabled": True,
        "hermes_agent_splunk_monitor_enabled": True,
        "hermes_agent_splunk_mcp_url": "https://mcp.example.test/splunk",
        "hermes_agent_slack_bot_token": "set",
        "hermes_agent_slack_app_token": "set",
        "hermes_agent_slack_home_channel": "set",
        "hermes_agent_slack_issues_channel": "set",
    }
    return yaml.safe_load(ENV.from_string(expression).render(**context))


def test_only_hindsight_dependent_crons_follow_hindsight_containment() -> None:
    service = yaml.safe_load((DEFAULTS / "48-service-pulse-and-splunk-health.yml").read_text())
    self_audit = yaml.safe_load((DEFAULTS / "145-self-audit.yml").read_text())
    pulse_jobs = service["hermes_agent_direct_cron_jobs_service_pulse"]
    pulse = next(job for job in pulse_jobs if job["name"] == "{{ hermes_agent_service_pulse_cron_name }}")
    splunk_health = next(job for job in pulse_jobs if job["name"] == "{{ hermes_agent_splunk_health_cron_name }}")
    audit = self_audit["hermes_agent_direct_cron_jobs_self_audit"][0]

    assert _enabled(pulse["enabled"], False)
    assert _enabled(pulse["enabled"], True)
    assert _enabled(splunk_health["enabled"], False)
    assert _enabled(splunk_health["enabled"], True)
    assert not _enabled(audit["enabled"], False)
    assert _enabled(audit["enabled"], True)
    hourly = yaml.safe_load((DEFAULTS / "49-llm-stats-hourly.yml").read_text())
    assert "hindsight_service_enabled | default(false) | bool" in hourly[
        "hermes_agent_llm_stats_hourly_enabled"
    ]
    job = hourly["hermes_agent_direct_cron_jobs_llm_stats"][0]
    assert not _enabled(job["enabled"], False)
    assert _enabled(job["enabled"], True)


def test_direct_cron_disabled_state_withdraws_installed_job() -> None:
    reconcile = (REPO_ROOT / "roles/hermes_agent/tasks/reconcile_direct_cron.yml").read_text()
    assert "Remove job not owned by this agent: {{ item.name }}" in reconcile
    assert "not (hermes_agent_direct_wanted | bool)" in reconcile
