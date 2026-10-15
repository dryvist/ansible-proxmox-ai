"""Disabled direct crons are paused only when the batch snapshot needs it."""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from jinja2 import Environment

TASKS = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "tasks"
ENV = Environment()
ENV.filters["regex_search"] = lambda value, pattern: (
    match.group(0) if (match := re.search(pattern, value)) else None
)
ENV.filters["regex_escape"] = re.escape


def _pause_runs(snapshot: dict[str, str], *, name: str, home: str) -> bool:
    tasks = yaml.safe_load((TASKS / "cron_pause_disabled.yml").read_text())
    task = next(task for task in tasks if task.get("name") == "Pause the direct crons declared disabled")
    conditions = task.get("when", [])
    conditions = [conditions] if isinstance(conditions, str) else conditions
    context = {
        "ansible_check_mode": False,
        "hermes_agent_hermes_home": "default-home",
        "hermes_agent_direct_cron_lists": snapshot,
        "item": {"name": name, "hermes_home": home},
    }
    results = []
    for condition in conditions:
        result = ENV.compile_expression(str(condition))(**context)
        assert isinstance(result, bool), f"condition returned {type(result).__name__}: {condition}"
        results.append(result)
    return all(results)


def test_already_paused_disabled_job_makes_no_pause_call():
    board = "ID: 1 [paused]\nName: disabled-job\n"
    assert not _pause_runs({"home-a": board}, name="disabled-job", home="home-a")


def test_active_disabled_job_still_makes_a_pause_call():
    board = "ID: 1 [active]\nName: disabled-job\n"
    assert _pause_runs({"home-a": board}, name="disabled-job", home="home-a")


def test_missing_disabled_job_makes_no_pause_call():
    board = "ID: 1 [active]\nName: another-job\n"
    assert not _pause_runs({"home-a": board}, name="disabled-job", home="home-a")


def test_every_condition_is_a_real_boolean():
    paused_board = "ID: 1 [paused]\nName: disabled-job\n"
    active_board = "ID: 1 [active]\nName: disabled-job\n"
    missing_name_board = "ID: 1 [active]\nName: another-job\n"

    assert not _pause_runs({"home-a": paused_board}, name="disabled-job", home="home-a")
    assert _pause_runs({"home-a": active_board}, name="disabled-job", home="home-a")
    assert not _pause_runs({"home-a": missing_name_board}, name="disabled-job", home="home-a")
    assert not _pause_runs({}, name="disabled-job", home="home-a")
