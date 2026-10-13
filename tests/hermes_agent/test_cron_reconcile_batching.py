"""A converge lists installed jobs and reads markers once; an unchanged job makes no remote call."""
from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

TASKS = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "tasks"
REMOTE_MODULES = ("ansible.builtin.command", "ansible.builtin.copy", "ansible.builtin.file")
ENV = Environment()
ENV.filters["bool"] = lambda v: v if isinstance(v, bool) else str(v).lower() in ("true", "yes", "1")


def _load(name: str) -> list[dict]:
    return yaml.safe_load((TASKS / name).read_text())


def _ran(*, wanted: bool, listed: bool, marker: str | None, changed: bool) -> set[str]:
    """Names of the remote/CLI tasks whose `when` passes for one job."""
    ctx = {
        "item": {"name": "job-a"},
        "hermes_agent_direct_wanted": wanted,
        "hermes_agent_direct_cron_list": {"stdout": "job-a" if listed else ""},
        "hermes_agent_direct_markers": {"job-a": marker} if marker else {},
        "hermes_agent_direct_marker_probe": {"changed": changed},
        "ansible_check_mode": False,
    }
    ran = set()
    for task in _load("reconcile_direct_cron.yml"):
        if not any(m in task for m in REMOTE_MODULES):
            continue
        conds = task.get("when", [])
        conds = [conds] if isinstance(conds, str) else conds
        if all(ENV.compile_expression(str(c))(**ctx) for c in conds):
            ran.add(task["name"].split(" {{")[0])
    return ran


def test_matching_marker_makes_no_cli_or_remote_call():
    assert _ran(wanted=True, listed=True, marker="sum", changed=False) == set()


def test_drifted_job_is_removed_recreated_and_recorded():
    assert _ran(wanted=True, listed=True, marker="old", changed=True) == {
        "Direct cron | Remove drifted",
        "Direct cron | Create",
        "Direct cron | Record desired state for",
    }


def test_absent_job_is_created_and_recorded_without_a_remove():
    assert _ran(wanted=True, listed=False, marker=None, changed=True) == {
        "Direct cron | Create",
        "Direct cron | Record desired state for",
    }


def test_withdrawn_job_is_removed_and_its_marker_cleared_even_after_a_stale_probe():
    assert _ran(wanted=False, listed=True, marker="sum", changed=True) == {
        "Direct cron | Remove job not owned by this agent:",
        "Direct cron | Clear desired-state marker for withdrawn",
    }


def test_unowned_job_with_nothing_installed_makes_no_remote_call():
    assert _ran(wanted=False, listed=False, marker=None, changed=False) == set()


def test_jobs_never_run_cron_list_and_the_batch_runs_it_per_home():
    per_job = [t for t in _load("reconcile_direct_cron.yml") if "cron list" in str(t)]
    assert per_job == []
    batch = next(t for t in _load("cron_reconcile.yml") if t.get("block"))["block"]
    listing = [t for t in batch if "cron list --all" in str(t)]
    assert len(listing) == 1 and "hermes_home" in listing[0]["loop"]
    assert sum("grep" in str(t) for t in batch) == 1
