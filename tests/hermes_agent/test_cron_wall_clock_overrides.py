"""Per-job aggregate cron limits stay within their schedule and router bounds."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

from _cron_pool_ceiling_shared import (
    cron_min_gap_minutes,
    min_job_interval_seconds,
    resolved_defaults,
    router_request_timeout_seconds,
    run_budget_seconds,
    wall_timeout_seconds,
)
from _role_files import role_tasks, template_text


ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
OVERRIDE_TASK = "Validate per-job aggregate cron wall-clock overrides"


def test_only_the_two_slow_daily_jobs_have_a_longer_limit() -> None:
    defaults = resolved_defaults()
    overrides = {
        job["name"]: job["wall_clock_seconds"]
        for job in defaults["hermes_agent_direct_cron_jobs"]
        if "wall_clock_seconds" in job
    }

    assert overrides == {
        "{{ hermes_agent_summary_cron_name }}": 2399,
        "{{ hermes_agent_blocked_parent_triage_digest_cron_name }}": 2399,
    }
    assert router_request_timeout_seconds() == 2400
    for job in defaults["hermes_agent_direct_cron_jobs"]:
        if "wall_clock_seconds" in job:
            assert (
                job["wall_clock_seconds"]
                < cron_min_gap_minutes(job["schedule"]) * 60
            )


def test_agent_run_budget_admits_the_longest_job_limit() -> None:
    defaults = resolved_defaults()
    ceiling = wall_timeout_seconds(defaults)
    overrides = [
        job["wall_clock_seconds"]
        for job in defaults["hermes_agent_direct_cron_jobs"]
        if "wall_clock_seconds" in job
    ]
    budget = run_budget_seconds(defaults)

    assert overrides and budget == max(overrides) > ceiling
    assert budget < router_request_timeout_seconds()
    assert budget < min_job_interval_seconds(defaults)
    for template in ("config.yaml.j2", "config-profile.yaml.j2"):
        assert (
            "run_budget_seconds: {{ hermes_agent_run_budget_seconds }}"
            in template_text(ROLE_ROOT, template)
        )


def test_per_job_validation_rejects_a_limit_at_the_router_ceiling() -> None:
    task = next(
        task for task in role_tasks(ROLE_ROOT) if task.get("name") == OVERRIDE_TASK
    )
    assertions = task["ansible.builtin.assert"]["that"]
    router_bound = next(
        expression
        for expression in assertions
        if "ai_router_request_timeout_seconds" in expression
    )
    environment = Environment(autoescape=False)
    environment.filters["cron_min_gap_minutes"] = cron_min_gap_minutes
    valid = environment.compile_expression(router_bound)

    router_timeout = router_request_timeout_seconds()
    assert valid(
        item={"wall_clock_seconds": router_timeout - 1},
        ai_router_request_timeout_seconds=router_timeout,
    )
    for too_long in (router_timeout, router_timeout + 1, 3300):
        assert not valid(
            item={"wall_clock_seconds": too_long},
            ai_router_request_timeout_seconds=router_timeout,
        )
    schedule_bound = next(
        expression
        for expression in assertions
        if "cron_min_gap_minutes" in expression
    )
    valid_schedule = environment.compile_expression(schedule_bound)
    assert valid_schedule(
        item={"wall_clock_seconds": 2399, "schedule": "25 12 * * *"}
    )
    assert not valid_schedule(
        item={"wall_clock_seconds": 3600, "schedule": "0 * * * *"}
    )


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
