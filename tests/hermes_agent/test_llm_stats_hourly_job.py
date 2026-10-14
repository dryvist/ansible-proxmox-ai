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
PROMPT_VAR = "hermes_agent_llm_stats_hourly_cron_prompt"
SEED_QUERIES = (
    r"""| tstats count AS events latest(_time) AS last_epoch WHERE index=llm earliest=-24h BY sourcetype | eval last_seen=strftime(last_epoch,"%F %T"), age_min=floor((now()-last_epoch)/60) | fields sourcetype events last_seen age_min | sort 0 - events""",
    r"""search index=llm sourcetype="litellm:proxy" earliest=-24h llm_request | dedup _raw | rex field=_raw "litellm\[\d+\]: (?<j>\{.*\})" | spath input=j | eval pt=tonumber(prompt_tokens), ct=tonumber(completion_tokens) | bin _time span=1h | stats sum(pt) AS prompt_tokens sum(ct) AS completion_tokens count AS requests BY _time model_group | sort 0 - _time model_group | eval hour=strftime(_time,"%m-%d %H:00") | fields hour model_group requests prompt_tokens completion_tokens""",
    r"""search index=llm sourcetype="litellm:proxy" earliest=-24h llm_request | dedup _raw | rex field=_raw "litellm\[\d+\]: (?<j>\{.*\})" | spath input=j | eval pt=tonumber(prompt_tokens), ct=tonumber(completion_tokens), serving_host=if(isnull(api_base) OR api_base IN ("","null"),"(none)",replace(replace(api_base,"^https?://",""),"[:/].*$","")), ok=if(status="success",1,0), bad=1-ok | bin _time span=1h | stats sum(ok) AS success sum(bad) AS failure count AS requests sum(pt) AS prompt_tokens sum(ct) AS completion_tokens BY _time serving_host | eval failure_pct=floor(1000*failure/requests)/10, hour=strftime(_time,"%m-%d %H:00") | sort 0 - _time serving_host | fields hour serving_host requests success failure failure_pct prompt_tokens completion_tokens""",
    r"""search index=llm sourcetype="litellm:proxy" earliest=-24h llm_request | dedup _raw | rex field=_raw "litellm\[\d+\]: (?<j>\{.*\})" | spath input=j | eval serving_host=if(isnull(api_base) OR api_base IN ("","null"),"(none)",replace(replace(api_base,"^https?://",""),"[:/].*$","")) | where error_class!="" | eval kind=case(error_class IN ("RateLimitError","RouterRateLimitError"),"429", error_class IN ("Timeout","ReadTimeout"),"timeout", true(),"other") | stats count AS errors BY serving_host kind error_class | sort 0 - errors""",
    r"""search index=llm sourcetype="litellm:proxy" earliest=-1h llm_request | dedup _raw | rex field=_raw "litellm\[\d+\]: (?<j>\{.*\})" | spath input=j | where status="success" | eval serving_host=if(isnull(api_base) OR api_base IN ("","null"),"(none)",replace(replace(api_base,"^https?://",""),"[:/].*$","")) | stats count AS requests latest(_time) AS last_epoch BY model_group model serving_host | eval last_seen=strftime(last_epoch,"%T") | sort 0 - requests | fields model_group model serving_host requests last_seen""",
    r"""search index=hermes earliest=-24h "API call #" | dedup _raw | rex field=_raw "^<\d+>\w+\s+\d+\s+[\d:]+\s+\S+\s+(?<profile>\S+)\s" | rex field=_raw "API call #(?<n>\d+): model=(?<model_group>\S+) provider=\S+ in=(?<tok_in>\d+) out=(?<tok_out>\d+)" | where isnotnull(n) | bin _time span=1h | stats count AS api_calls sum(tok_in) AS tokens_in sum(tok_out) AS tokens_out BY _time host profile model_group | eval hour=strftime(_time,"%m-%d %H:00") | sort 0 - _time host profile model_group | fields hour host profile model_group api_calls tokens_in tokens_out""",
)


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


def test_seed_queries_survive_the_cron_prompt_delivery_path() -> None:
    tasks = yaml.safe_load((ROLE / "tasks" / "reconcile_direct_cron.yml").read_text())
    load_prompt = next(
        task["ansible.builtin.set_fact"]["hermes_agent_direct_prompt"]
        for task in tasks
        if str(task.get("name", "")).startswith("Direct cron | Load prompt + append")
    )
    environment = Environment(autoescape=False)
    environment.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(
        pattern, replacement, value
    )
    environment.globals["lookup"] = lambda source, name, **kwargs: (
        DEFAULTS[name] if source == "vars" else ""
    )
    delivered = environment.from_string(load_prompt).render(
        item={"prompt_var": PROMPT_VAR},
        hermes_agent_direct_deliver="slack:stats",
        hermes_agent_prompts_path="/prompts",
    )
    for query in SEED_QUERIES:
        assert query in delivered

    build_argv = next(
        task["ansible.builtin.set_fact"]["hermes_agent_direct_argv"]
        for task in tasks
        if str(task.get("name", "")).startswith("Direct cron | Build create argv")
    )
    create = next(
        task["ansible.builtin.command"]
        for task in tasks
        if str(task.get("name", "")).startswith("Direct cron | Create {{ item.name }}")
    )
    assert "hermes_agent_direct_prompt" in build_argv
    assert create["argv"] == "{{ hermes_agent_direct_argv }}"


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
    assert not re.search(r"https?://[A-Za-z0-9.-]", prompt)
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
