"""Contract checks for the W3 recurring-job-catalog additions (agents file
S1.2/S1.6): pricing-diff, blocked-parent-triage-digest, router-capacity-digest,
and backlog-sweep.

Runs bare (`python3 tests/hermes_agent/test_recurring_catalog.py`) or under
pytest.
"""
import re
from pathlib import Path

from _role_files import role_defaults, role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
PROMPT_CATALOG_TASKS = role_tasks_text(ROLE, "prompt_catalog.yml")

NEW_JOB_NAME_VARS = {
    "hermes_agent_pricing_diff_cron_name",
    "hermes_agent_blocked_parent_triage_digest_cron_name",
    "hermes_agent_router_capacity_digest_cron_name",
    "hermes_agent_backlog_sweep_cron_name",
}


def _job(name_var):
    wanted = "{{ " + name_var + " }}"
    for job in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        if job["name"] == wanted:
            return job
    raise AssertionError(f"no catalog entry has name {wanted!r}")


def test_all_four_new_jobs_are_in_the_reassembled_catalog():
    for name_var in NEW_JOB_NAME_VARS:
        _job(name_var)  # raises if missing


def test_every_new_job_has_a_five_field_cron_schedule():
    for name_var in NEW_JOB_NAME_VARS:
        schedule_var = name_var.replace("_cron_name", "_cron_schedule")
        schedule = DEFAULTS[schedule_var]
        assert len(schedule.split()) == 5, (schedule_var, schedule)


def test_pricing_diff_never_touches_the_model_registry():
    prompt = DEFAULTS["hermes_agent_pricing_diff_cron_prompt"]
    assert "openrouter.ai/api/v1/models" in prompt
    assert "Never propose or attempt to edit the router registry" in prompt


def test_blocked_parent_triage_is_observe_only():
    prompt = DEFAULTS["hermes_agent_blocked_parent_triage_digest_cron_prompt"]
    assert "recompute_ready" in prompt, "must name the actual Vikunja 1523 mechanism"
    assert "never mark a parent done, archived, or blocked yourself" in prompt


def test_backlog_sweep_never_closes_what_it_did_not_create():
    prompt = DEFAULTS["hermes_agent_backlog_sweep_cron_prompt"]
    assert "NEVER close, resolve, or move a ticket to pending-close yourself" in prompt
    assert "Vikunja items are out of scope for this job today" in prompt, (
        "no profile has Vikunja read access yet — this must stay honest about that")


def test_router_capacity_digest_reads_the_log_platform_not_the_host():
    prompt = DEFAULTS["hermes_agent_router_capacity_digest_cron_prompt"]
    assert "llm-serving-share" in prompt
    assert "read it from the log platform (never the host)" in prompt.lower()


def test_daily_status_prompt_appends_the_kanban_summary():
    assert "hermes_agent_daily_status_kanban_summary" in PROMPT_CATALOG_TASKS
    summary = DEFAULTS["hermes_agent_daily_status_kanban_summary"]
    assert "kanban" in summary
    assert re.search(r"localhost:\{\{ ?hermes_agent_dashboard_port ?\}\}", summary), (
        "the dashboard link must be the same rendered var the endpoints block uses, "
        "never a hardcoded host"
    )


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
