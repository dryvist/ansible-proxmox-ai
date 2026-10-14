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
    job = _direct_job(NAME_VAR)
    schedule = DEFAULTS["hermes_agent_llm_stats_hourly_cron_schedule"]
    assert job["schedule"] == "{{ hermes_agent_llm_stats_hourly_cron_schedule }}"
    assert schedule == "54 * * * *"
    assert job["system"] == "splunk"
    assert "model" not in job
    assert re.fullmatch(r"[0-5]?\d \* \* \* \*", schedule)
    digest_ticks = range(0, 60, int(DEFAULTS["hermes_agent_kanban_digest_interval_minutes"]))
    assert int(schedule.split()[0]) not in digest_ticks


def test_prompt_carries_the_memory_and_feedback_protocol() -> None:
    prompt = DEFAULTS["hermes_agent_llm_stats_hourly_cron_prompt"]
    for key in ("baselines", "sources", "feedback", "last"):
        assert f"llm-stats:{key}" in prompt
    assert "reaction:added" in prompt
    assert '"Gaps:"' in prompt


def test_prompt_budgets_queries_and_report_time() -> None:
    prompt = DEFAULTS["hermes_agent_llm_stats_hourly_cron_prompt"]
    assert "at most 10 splunk_run_query calls" in prompt
    assert "After two failed calls to splunk_run_query, including timeouts, stop all Splunk queries" in prompt
    assert 'Publish a partial report using recalled baselines, label them "recalled baseline,"' in prompt
    assert 'Include one "Gaps:" line naming unavailable current metrics' in prompt
    assert "within 15 minutes" in prompt
    assert "Stop querying by minute 10" in prompt


def test_prompt_uses_real_memory_tools_and_caps_all_tool_calls() -> None:
    prompt = DEFAULTS["hermes_agent_llm_stats_hourly_cron_prompt"]
    assert "hindsight_recall" in prompt
    assert "hindsight_retain" in prompt
    assert "Never use the built-in `memory` tool in any turn" in prompt
    assert "Do not use `execute_code`, `terminal` or file-search tools" in prompt
    assert "at most 25 tool calls in total" in prompt
    assert "At call 20, stop gathering and prepare the final report" in prompt
    assert "Call each tool directly, one tool per call" in prompt
    assert "Do not use `tool_call` batching" in prompt


def test_prompt_makes_the_report_the_final_response_and_saves_feedback_per_turn() -> None:
    prompt = DEFAULTS["hermes_agent_llm_stats_hourly_cron_prompt"]
    assert "cron platform posts this run's final response to the Slack channel automatically" in prompt
    assert "that final response IS the report" in prompt
    assert "Never call a send or post tool" in prompt
    assert "never end with a status line" in prompt
    assert "`hindsight_retain` call before writing the final response" in prompt
    assert "the final response (the report) is the last thing this run produces" in prompt.lower()

    feedback = prompt.split("FEEDBACK.", 1)[1].split("FORMAT.", 1)[0]
    assert "Make ONE `hindsight_retain` call" in feedback
    assert 'dated "llm-stats:feedback" line naming the sections that report had' in feedback
    assert "then answer with one short acknowledgement" in feedback
    assert "using the end-of-run `hindsight_retain` call" not in feedback


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
