"""The hourly AI news job belongs to pepper, is wired into the job list and is gated on its channel."""

from __future__ import annotations

from pathlib import Path

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"


def test_ai_news_hourly_job_is_wired_and_gated_on_its_channel() -> None:
    defaults = role_defaults(ROLE)
    (job,) = defaults["hermes_agent_direct_cron_jobs_ai_news_hourly"]

    assert job in defaults["hermes_agent_direct_cron_jobs"]
    assert job["prompt_var"] in defaults["hermes_agent_catalog_prompt_sources"]
    assert job["deliver"] == "slack:{{ hermes_agent_ai_news_hourly_channel }}"
    assert "hermes_agent_ai_news_hourly_channel | length > 0" in job["enabled"]
    assert job["identities"] == ["pepper"]
    assert "hermes_agent_pepper_research_enabled | bool" in job["enabled"]
    assert "hermes_agent_pepper_research_toolsets" in job["enabled_toolsets"]
    minute = defaults["hermes_agent_ai_news_hourly_cron_schedule"].split()[0]
    assert minute not in {"0", "15", "30", "45"}
