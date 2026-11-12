"""System-owned cron jobs move with the published agent roster."""

from __future__ import annotations

import ast
from pathlib import Path

from jinja2 import Environment
import yaml

from _role_files import include_file, role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
SPLUNK_JOBS = {
    "anomaly-hunt",
    "llm-stats-hourly",
    "router-capacity-digest",
    "splunk-deepdive",
    "splunk-error-digest",
    "splunk-health",
    "splunk-parsing",
    "splunk-security",
    "splunk-security-digest",
    "splunk-status-digest",
    "splunk-triage",
    "wired-trajectory-watch",
}


def _task_items(task_file: str) -> list[dict]:
    tasks = yaml.safe_load((ROLE / "tasks" / task_file).read_text())
    return [
        task["vars"]["item"]
        for task in tasks
        if include_file(task)
        in ("reconcile_direct_cron.yml", "reconcile_enqueuer_cron.yml")
        and "item" in task.get("vars", {})
    ]


def _jobs() -> list[dict]:
    return (
        DEFAULTS["hermes_agent_direct_cron_jobs"]
        + DEFAULTS["hermes_agent_triage_jobs"]
        + _task_items("cron_reconcile.yml")
        + _task_items("cron_reconcile_pr_repo_crawl.yml")
    )


def _job_name(job: dict) -> str:
    name = job["name"]
    return Environment(autoescape=False).from_string(name).render(**DEFAULTS)


def _identities(job: dict, identity: str, owners: dict[str, str]) -> list[str]:
    context = dict(DEFAULTS, item=job, hermes_agent_id=identity, ai_agent_owner=owners)
    rendered = Environment(autoescape=False).from_string(
        DEFAULTS["hermes_agent_job_identities"]
    ).render(**context)
    return ast.literal_eval(rendered)


def _direct_owned_names(identity: str, owners: dict[str, str]) -> set[str]:
    context = dict(DEFAULTS, hermes_agent_id=identity, ai_agent_owner=owners)
    rendered = Environment(autoescape=False).from_string(
        DEFAULTS["hermes_agent_direct_cron_jobs_owned"]
    ).render(**context)
    return {_job_name(job) for job in ast.literal_eval(rendered)}


def test_empty_roster_preserves_every_job_identity() -> None:
    for job in _jobs():
        assert _identities(job, "hermes", {}) == job.get(
            "identities", DEFAULTS["hermes_agent_job_identities_default"]
        ), _job_name(job)

    for identity in ("hermes", "hermes-private", "donna"):
        expected = {
            _job_name(job)
            for job in DEFAULTS["hermes_agent_direct_cron_jobs"]
            if identity in _identities(job, identity, {})
        }
        assert _direct_owned_names(identity, {}) == expected


def test_private_identity_owns_four_local_assistant_catalog_jobs() -> None:
    expected = {
        "assistant-pulse",
        "assistant-task-triage",
        "private-assistant-inbox-groom",
        "private-assistant-evening-wrap",
    }
    jobs = {
        _job_name(job): job
        for job in DEFAULTS["hermes_agent_direct_cron_jobs"]
        if "hermes-private" in _identities(job, "hermes-private", {})
    }

    assert set(jobs) == expected
    context = dict(
        DEFAULTS,
        hermes_agent_donna_deliver="local",
        hermes_agent_donna_failure_deliver="local",
    )
    for name, job in jobs.items():
        deliver = Environment(autoescape=False).from_string(job["deliver"]).render(**context)
        failure_deliver = Environment(autoescape=False).from_string(
            job["failure_deliver"]
        ).render(**context)
        assert deliver == "local", name
        assert failure_deliver == "local", name
        assert job.get("model") is None, name
        assert job.get("hermes_home") is None, name

    core_vars_path = REPO_ROOT / "inventory" / "group_vars" / "all" / "00-core.yml"
    core_vars = core_vars_path.read_text()
    assert "hermes_agent_cron_model: medium" in core_vars

    schedules = _render_schedule(jobs)
    assert schedules == {
        "assistant-pulse": "39 * * * *",
        "assistant-task-triage": "27 * * * *",
        "private-assistant-inbox-groom": "11 10 * * *",
        "private-assistant-evening-wrap": "11 23 * * *",
    }
    for schedule in schedules.values():
        minute = int(schedule.split()[0])
        assert minute % 2 == 1
        assert minute not in {0, 15, 30, 45}


def _render_schedule(jobs: dict[str, dict]) -> dict[str, str]:
    return {
        name: Environment(autoescape=False)
        .from_string(job["schedule"])
        .render(**DEFAULTS)
        for name, job in jobs.items()
    }


def test_private_local_jobs_reconcile_without_slack_configuration() -> None:
    runs = Environment(autoescape=False).from_string(
        DEFAULTS["hermes_agent_direct_cron_reconcile_runs"]
    )
    no_slack = {
        "hermes_agent_digest_slack_channel": "",
        "hermes_agent_slack_bot_token": "",
        "hermes_agent_slack_app_token": "",
        "hermes_agent_slack_home_channel": "",
    }

    for identity, expected in (
        ("hermes-private", "True"),
        ("pepper", "True"),
        ("hermes", "False"),
    ):
        context = dict(DEFAULTS, hermes_agent_id=identity, **no_slack)
        assert runs.render(**context).strip() == expected


def test_splunk_owner_takes_only_splunk_jobs() -> None:
    jobs = _jobs()
    tagged = {_job_name(job) for job in jobs if job.get("system") == "splunk"}
    assert tagged == SPLUNK_JOBS

    for job in jobs:
        baseline = job.get("identities", DEFAULTS["hermes_agent_job_identities_default"])
        expected = ["splunk-heimdall"] if job.get("system") == "splunk" else baseline
        assert _identities(job, "splunk-heimdall", {"splunk": "splunk-heimdall"}) == expected

    owners = {"splunk": "splunk-heimdall"}
    for identity in ("hermes", "hermes-private", "donna", "splunk-heimdall"):
        expected_direct = {
            _job_name(job)
            for job in DEFAULTS["hermes_agent_direct_cron_jobs"]
            if identity in _identities(job, identity, owners)
        }
        assert _direct_owned_names(identity, owners) == expected_direct

    # The direct-cron ownership decision is the shared definition in defaults,
    # which is where the identities are read; the enqueuer path reads them itself.
    direct = (ROLE / "tasks" / "reconcile_direct_cron.yml").read_text()
    assert "hermes_agent_direct_job_wanted" in direct
    assert "hermes_agent_job_identities" in DEFAULTS["hermes_agent_direct_job_wanted"]
    enqueuer = (ROLE / "tasks" / "reconcile_enqueuer_cron.yml").read_text()
    assert "hermes_agent_job_identities" in enqueuer


def test_zammad_review_is_wanted_by_no_identity() -> None:
    # The wanted definition (enabled and identity), not the ownership list,
    # which ignores enabled. The positive control proves a True renders too.
    env = Environment(autoescape=False)
    env.filters["bool"] = lambda value: (
        value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")
    )
    wanted = env.from_string(DEFAULTS["hermes_agent_direct_job_wanted"])
    control = wanted.render(
        item={"enabled": True},
        hermes_agent_id="hermes",
        hermes_agent_job_identities=["hermes"],
    )
    assert control.strip() == "True"

    job = next(
        job for job in DEFAULTS["hermes_agent_direct_cron_jobs"]
        if _job_name(job) == "zammad-review"
    )
    assert job["enabled"] is False
    for owners in ({}, {"splunk": "splunk-heimdall"}):
        for identity in ("hermes", "hermes-private", "donna", "splunk-heimdall", "pepper"):
            rendered = wanted.render(
                item=job,
                hermes_agent_id=identity,
                hermes_agent_job_identities=_identities(job, identity, owners),
            )
            assert rendered.strip() == "False", (identity, owners)
