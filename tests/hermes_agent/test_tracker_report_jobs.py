"""The tracker reporter is one read-only profile with two jobs that differ only in their run tail."""

from __future__ import annotations

import yaml

from _role_files import role_defaults
from test_homeassistant_plugin_contract import _profile_context
from test_profile_contract import ROLE_ROOT, _jinja_env, _profiles

PROFILE = "tracker-reporter"
PER_JOB = {"name", "schedule", "prompt_tail", "deliver"}


def _jobs() -> list[dict]:
    return role_defaults(ROLE_ROOT)["hermes_agent_direct_cron_jobs_tracker_report"]


def test_the_profile_reads_both_trackers_and_holds_no_credential_env() -> None:
    profile = next(p for p in _profiles() if p["name"] == PROFILE)
    assert profile["mcp"] == ["vikunja", "zammad"]
    assert profile["env"] == []


def test_the_two_jobs_differ_only_in_their_run_specific_fields() -> None:
    first, second = _jobs()
    shared = lambda job: {key: value for key, value in job.items() if key not in PER_JOB}  # noqa: E731
    assert shared(first) == shared(second)
    assert first["prompt_tail"] != second["prompt_tail"]
    assert all(job["prompt_tail"].startswith("REPORT: ") for job in (first, second))


def test_no_job_starts_on_a_quarter_hour() -> None:
    assert all(job["schedule"].split()[0] not in {"0", "15", "30", "45"} for job in _jobs())


def test_only_the_reporter_renders_the_router_incident_tool_server() -> None:
    template = _jinja_env().get_template("config-profile.yaml.j2")
    for profile in _profiles():
        rendered = template.render(hermes_agent_profile=profile, **_profile_context(False))
        servers = yaml.safe_load(rendered).get("mcp_servers") or {}
        assert ("zammad" in servers) is (profile["name"] == PROFILE), profile["name"]
        if profile["name"] == PROFILE:
            assert servers["zammad"]["headers"]["Authorization"] == "Bearer ${HERMES_AGENT_MODEL_API_KEY}"
