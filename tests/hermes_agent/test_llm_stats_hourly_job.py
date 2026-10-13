"""Contract checks for the hourly local-LLM stats cron and the Splunk-profile
memory bank it recalls from.

Runs bare (`python3 tests/hermes_agent/test_llm_stats_hourly_job.py`) or under
pytest. The minute-collision guarantee lives in test_cron_stagger.py, the
Slack reaction render in test_a2a_config.py, and the profile-bank render in
test_hindsight_readiness_contract.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment

from _alert_routing_shared import _direct_job
from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
NAME_VAR = "hermes_agent_llm_stats_hourly_cron_name"


def test_job_is_a_splunk_profile_job_delivered_to_the_splunk_channel() -> None:
    job = _direct_job(NAME_VAR)
    assert job["skill"] == "dryvist/splunk-monitor"
    assert job["hermes_home"].endswith("/profiles/splunk-admin")
    assert "hermes_agent_slack_splunk_channel" in job["deliver"]
    assert "hermes_agent_llm_stats_hourly_enabled" in job["enabled"]
    assert "hermes_agent_splunk_mcp_url" in job["enabled"]


def test_job_runs_hourly_off_the_board_digest_tick() -> None:
    schedule = DEFAULTS["hermes_agent_llm_stats_hourly_cron_schedule"]
    assert re.fullmatch(r"[0-5]?\d \* \* \* \*", schedule)
    digest_ticks = range(0, 60, int(DEFAULTS["hermes_agent_kanban_digest_interval_minutes"]))
    assert int(schedule.split()[0]) not in digest_ticks


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


def _splunk_bank(agent_id: str, owners: dict) -> str:
    expression = DEFAULTS["hermes_agent_splunk_memory_bank_id"]
    return (
        Environment()
        .from_string(expression)
        .render(hermes_agent_id=agent_id, hermes_agent_memory_bank_id=agent_id, ai_agent_owner=owners)
        .strip()
    )


def test_splunk_bank_is_the_agent_bank_on_the_owner_and_dedicated_elsewhere() -> None:
    owners = {"splunk": "splunk-owner"}
    assert _splunk_bank("splunk-owner", owners) == "splunk-owner"
    assert _splunk_bank("hermes", owners) == "hermes-splunk"
    assert _splunk_bank("hermes", {}) == "hermes-splunk"


def test_only_the_splunk_profile_sets_a_bank_and_the_config_task_passes_it() -> None:
    profiles = {p["name"]: p for p in DEFAULTS["hermes_agent_profiles"]}
    assert profiles["splunk-admin"]["memory_bank_id"] == "{{ hermes_agent_splunk_memory_bank_id }}"
    assert all("memory_bank_id" not in p for name, p in profiles.items() if name != "splunk-admin")

    tasks = yaml.safe_load((ROLE / "tasks" / "profiles.yml").read_text())
    task = next(t for t in tasks if str(t.get("name", "")).startswith("Deploy the hindsight provider config"))
    assert task["vars"]["hermes_agent_profile_memory_bank_id"] == (
        "{{ item.memory_bank_id | default(hermes_agent_memory_bank_id) }}"
    )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
