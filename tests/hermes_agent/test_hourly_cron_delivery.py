"""Hourly assistant jobs use local output; fabric status retains its Slack split."""

from pathlib import Path

from jinja2 import Environment

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
TEMPLATE = Environment(autoescape=False)

SCHEDULES = {
    "homelab-ai-fabric-status": "4 * * * *",
    "assistant-task-triage": "27 * * * *",
    "assistant-daily-brief": "57 * * * *",
}
LOCAL_OUTPUT_JOBS = {"assistant-task-triage", "assistant-daily-brief"}


def _render(value: object) -> str:
    return TEMPLATE.from_string(str(value)).render(**DEFAULTS).strip()


def _jobs_by_name() -> dict[str, dict]:
    return {
        _render(job["name"]): job
        for job in DEFAULTS["hermes_agent_direct_cron_jobs"]
    }


def test_requested_hourly_schedules_are_loaded_from_role_defaults() -> None:
    jobs = _jobs_by_name()
    assert set(SCHEDULES) <= jobs.keys()

    for name, expected_schedule in SCHEDULES.items():
        assert _render(jobs[name]["schedule"]) == expected_schedule


def test_hourly_jobs_use_local_output_for_success_and_failure() -> None:
    jobs = _jobs_by_name()

    for name in LOCAL_OUTPUT_JOBS:
        job = jobs[name]
        assert _render(job.get("deliver", "")) == "local", name
        assert _render(job.get("failure_deliver", "")) == "local", name
        assert "channel_when_healthy" not in job, name


def test_fabric_status_keeps_its_slack_outcome_split() -> None:
    job = _jobs_by_name()["homelab-ai-fabric-status"]
    assert job["deliver"] == "slack:{{ hermes_agent_slack_hermes_all_channel }}"
    assert job["channel_when_healthy"] == "slack:{{ hermes_agent_slack_noise_channel }}"
