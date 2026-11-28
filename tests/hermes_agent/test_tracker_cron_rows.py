"""The three tracker-agent schedules: one fixed prompt each, fleet-free minutes.

The rows live in 45-tracker-agent-cron-jobs.yml and reach the gateway through
the ``hermes_agent_direct_cron_jobs`` join in 43-direct-cron-jobs-core.yml.
What is pinned here is the part the generic fleet tests cannot express for
three specific rows: each row names its agent's public router model, one
switch gates all three rows, the schedules stay off the quarter-hour ticks
and at least four minutes apart, and each prompt is a prompt catalog file.

The fleet-wide half of the minute choice — no start minute any other enabled
job already uses — is test_cron_stagger.py; the minutes in the defaults file
were chosen so both that guard and the assertions here hold together.
"""

from __future__ import annotations

from pathlib import Path

from _role_files import role_defaults
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)

_JINJA_ENV = Environment(autoescape=False)
_JINJA_ENV.filters["bool"] = bool  # an Ansible filter, not a plain-Jinja one

SWITCH = "hermes_agent_tracker_cron_enabled"

ROWS = {
    "vikunja-triage-sweep": {
        "schedule_var": "hermes_agent_tracker_triage_cron_schedule",
        "prompt_var": "hermes_agent_tracker_triage_cron_prompt",
        "model": "agent-vikunja-triage",
    },
    "vikunja-nightly-maintenance": {
        "schedule_var": "hermes_agent_tracker_nightly_cron_schedule",
        "prompt_var": "hermes_agent_tracker_nightly_cron_prompt",
        "model": "agent-vikunja-nightly",
    },
    "vikunja-windows-sweep": {
        "schedule_var": "hermes_agent_tracker_windows_cron_schedule",
        "prompt_var": "hermes_agent_tracker_windows_cron_prompt",
        "model": "agent-vikunja-windows",
    },
}

QUARTER_HOUR_MINUTES = {0, 15, 30, 45}


def _rows() -> dict[str, dict]:
    return {row["name"]: row for row in DEFAULTS["hermes_agent_tracker_cron_jobs"]}


def _render(value: object, **overrides) -> str:
    context = {**DEFAULTS, **overrides}
    return _JINJA_ENV.from_string(str(value)).render(**context).strip()


def _row_minutes(schedule: str) -> set[int]:
    minute_field, hour_field = schedule.split()[0], schedule.split()[1]
    assert hour_field == "*", f"{schedule!r} must fire in every hour"
    return {int(part) for part in minute_field.split(",")}


def test_the_three_rows_name_their_agent_model_prompt_and_shared_switch() -> None:
    rows = _rows()
    assert set(rows) == set(ROWS)
    for name, expected in ROWS.items():
        row = rows[name]
        assert row["schedule"] == "{{ " + expected["schedule_var"] + " }}"
        assert row["prompt_var"] == expected["prompt_var"]
        assert row["model"] == expected["model"]
        assert row["skill"] == ""
        assert row["enabled_toolsets"] == []
        assert row["deliver"] == "{{ hermes_agent_tracker_cron_deliver }}"
        assert row["enabled"] == "{{ hermes_agent_tracker_cron_enabled | bool }}"


def test_the_shared_deliver_target_is_the_daily_status_row_expression() -> None:
    daily_status = next(
        row
        for row in DEFAULTS["hermes_agent_direct_cron_jobs_core"]
        if row["name"] == "{{ hermes_agent_daily_status_cron_name }}"
    )
    assert DEFAULTS["hermes_agent_tracker_cron_deliver"] == daily_status["deliver"]


def test_the_shared_switch_renders_all_three_rows_disabled_then_enabled() -> None:
    assert DEFAULTS[SWITCH] is False
    for row in _rows().values():
        assert _render(row["enabled"]) == "False"
        assert _render(row["enabled"], **{SWITCH: True}) == "True"


def test_each_schedule_fires_once_an_hour_off_the_quarter_hour() -> None:
    for name in ROWS:
        schedule = _render(_rows()[name]["schedule"])
        minutes = _row_minutes(schedule)
        assert len(minutes) == 1, f"{name}: {schedule!r} is not an hourly cadence"
        assert not minutes & QUARTER_HOUR_MINUTES, f"{name}: {schedule!r}"


def test_any_two_tracker_rows_start_at_least_four_minutes_apart() -> None:
    schedules = [
        sorted(_row_minutes(_render(_rows()[name]["schedule"]))) for name in ROWS
    ]
    for index, first in enumerate(schedules):
        for second in schedules[index + 1 :]:
            for a in first:
                for b in second:
                    gap = min((a - b) % 60, (b - a) % 60)
                    assert gap >= 4, (
                        f"{first} vs {second}: minutes {a:02d} and {b:02d} "
                        f"are only {gap} apart"
                    )


def test_the_joined_fleet_list_carries_each_row_exactly_once() -> None:
    joined_names = [
        entry["name"] for entry in DEFAULTS["hermes_agent_direct_cron_jobs"]
    ]
    for name in ROWS:
        assert joined_names.count(name) == 1, name


def test_each_prompt_comes_from_the_prompt_catalog() -> None:
    sources = DEFAULTS["hermes_agent_catalog_prompt_sources"]
    for expected in ROWS.values():
        assert sources[expected["prompt_var"]]["prompt_file"].startswith("hermes-vikunja-")
