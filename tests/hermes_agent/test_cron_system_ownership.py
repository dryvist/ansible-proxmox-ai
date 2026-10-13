"""System-owned cron jobs move with the published agent roster."""

from __future__ import annotations

import ast
from pathlib import Path

from jinja2 import Environment
import yaml

from _role_files import role_defaults

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
        if task.get("ansible.builtin.include_tasks")
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

    for task_file in ("reconcile_direct_cron.yml", "reconcile_enqueuer_cron.yml"):
        task_text = (ROLE / "tasks" / task_file).read_text()
        assert "hermes_agent_job_identities" in task_text
