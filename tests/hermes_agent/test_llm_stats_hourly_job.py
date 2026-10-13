"""Contract checks for the hourly local-LLM stats cron, the Slack reaction
trigger it learns from, and the Splunk-profile memory bank it recalls from.

Runs bare (`python3 tests/hermes_agent/test_llm_stats_hourly_job.py`) or under
pytest. The minute-collision guarantee lives in test_cron_stagger.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
NAME_VAR = "hermes_agent_llm_stats_hourly_cron_name"


def _job() -> dict:
    wanted = "{{ " + NAME_VAR + " }}"
    for job in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        if job["name"] == wanted:
            return job
    raise AssertionError(f"no hermes_agent_direct_cron_jobs entry has name {wanted!r}")


def test_job_is_a_splunk_profile_job_delivered_to_the_splunk_channel() -> None:
    job = _job()
    assert job["system"] == "splunk"
    assert job["skill"] == "dryvist/splunk-monitor"
    assert job["hermes_home"].endswith("/profiles/splunk-admin")
    assert "hermes_agent_slack_splunk_channel" in job["deliver"]
    assert "hermes_agent_llm_stats_hourly_enabled" in job["enabled"]
    assert "hermes_agent_splunk_mcp_url" in job["enabled"]


def test_job_runs_hourly_on_a_single_minute() -> None:
    minute, hour, dom, month, dow = DEFAULTS["hermes_agent_llm_stats_hourly_cron_schedule"].split()
    assert minute.isdigit() and 0 <= int(minute) < 60
    assert (hour, dom, month, dow) == ("*", "*", "*", "*")


def test_prompt_carries_the_memory_and_feedback_protocol() -> None:
    prompt = DEFAULTS["hermes_agent_llm_stats_hourly_cron_prompt"]
    for key in ("baselines", "sources", "feedback", "last"):
        assert f"llm-stats:{key}" in prompt
    assert "reaction:added" in prompt
    assert "gaps" in prompt


def test_prompt_names_no_endpoint_or_channel_id() -> None:
    prompt = DEFAULTS["hermes_agent_llm_stats_hourly_cron_prompt"]
    assert "http" not in prompt
    assert not re.search(r"\bC[A-Z0-9]{8,}\b", prompt)
    assert not re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", prompt)


def test_slack_reaction_triggers_render_into_the_slack_extra_block() -> None:
    assert DEFAULTS["hermes_agent_slack_reaction_triggers"] is True
    template = (ROLE / "templates" / "config.yaml.j2").read_text()
    assert "reaction_triggers: true" in template
    assert "hermes_agent_slack_reaction_triggers" in template


def test_splunk_profile_has_its_own_memory_bank_and_others_keep_the_agent_bank() -> None:
    profiles = {p["name"]: p for p in DEFAULTS["hermes_agent_profiles"]}
    assert profiles["splunk-admin"]["memory_bank_id"] == "{{ hermes_agent_splunk_memory_bank_id }}"
    bank = " ".join(DEFAULTS["hermes_agent_splunk_memory_bank_id"].split())
    assert "hermes_agent_id ~ '-splunk'" in bank
    assert "(ai_agent_owner | default({})).get('splunk')" in bank
    assert DEFAULTS["hermes_agent_memory_bank_id"] == "{{ hermes_agent_id }}"
    assert all("memory_bank_id" not in p for n, p in profiles.items() if n != "splunk-admin")
    template = (ROLE / "templates" / "hindsight-config.json.j2").read_text()
    assert "hermes_agent_profile_memory_bank_id | default(hermes_agent_memory_bank_id)" in template
    tasks = (ROLE / "tasks" / "profiles.yml").read_text()
    assert "item.memory_bank_id | default(hermes_agent_memory_bank_id)" in tasks


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
