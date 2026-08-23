"""Every `hermes cron remove` task tolerates an already-absent job.

`hermes cron remove` exits 1 with "Job with ID or name '...' not found" when
the job is already gone — but absent IS the desired state, so a converge must
not abort on it (a substring-matched list guard, or a race with the live
agent, makes the second run hit exactly this). These tests enumerate every
cron-remove command task in the role and evaluate its actual failed_when /
changed_when expressions against the three outcomes — the run-twice case at
the expression level: first run removes (rc=0, changed), second run finds
nothing (rc=1 not-found, ok and unchanged), and a real removal error still
fails.
"""

from __future__ import annotations

from typing import Any

import pytest
from jinja2 import Environment

from conftest import ROLE_ROOT, role_tasks

NOT_FOUND_STDOUT = (
    "Failed to remove job: Job with ID or name 'review' not found. "
    "Use cronjob(action='list') to inspect jobs."
)


def _cron_remove_tasks() -> list[dict[str, Any]]:
    return [
        task
        for task in role_tasks(ROLE_ROOT)
        if "cron remove" in str(task.get("ansible.builtin.command", ""))
    ]


def _evaluate(expression: Any, register: str, result: dict[str, Any]) -> bool:
    # Plain jinja2 (no Ansible filters): the expressions under test use only
    # rc/stdout comparisons, which render to literal True/False.
    rendered = (
        Environment(autoescape=False)
        .from_string("{{ " + str(expression).strip() + " }}")
        .render(**{register: result})
    )
    assert rendered in ("True", "False"), rendered
    return rendered == "True"


def test_role_still_has_cron_remove_tasks() -> None:
    # Positive control for the parametrized enumeration below.
    assert len(_cron_remove_tasks()) >= 6


@pytest.mark.parametrize(
    "task", _cron_remove_tasks(), ids=lambda task: task["name"]
)
def test_cron_remove_tolerates_already_absent(task: dict[str, Any]) -> None:
    register = task.get("register")
    assert register, f"missing register: {task['name']}"
    failed_when = task.get("failed_when")
    changed_when = task.get("changed_when")
    assert failed_when and changed_when, f"missing guard: {task['name']}"

    # Second run / already absent: rc=1 + not-found is the desired state.
    absent = {"rc": 1, "stdout": NOT_FOUND_STDOUT}
    assert not _evaluate(failed_when, register, absent)
    assert not _evaluate(changed_when, register, absent)

    # First run: a real removal is a change and not a failure.
    removed = {"rc": 0, "stdout": "Removed job 'review'"}
    assert not _evaluate(failed_when, register, removed)
    assert _evaluate(changed_when, register, removed)

    # A real error must still fail — the guard is not a blanket ignore.
    broken = {"rc": 1, "stdout": "Failed to remove job: store is corrupt"}
    assert _evaluate(failed_when, register, broken)


def test_profile_tick_trigger_name_is_static() -> None:
    # This task's own loop supplies `item`, but a task NAME templates before
    # the loop does — the log rendered "<< error 1 - 'item' is undefined >>".
    task = next(
        task
        for task in role_tasks(ROLE_ROOT)
        if "profile cron tick trigger" in str(task.get("name", ""))
    )
    assert "{{" not in task["name"]
