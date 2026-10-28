"""The watchdog's board read-back against `hermes cron list --all` output.

fixtures/cron-list-all-v0225.txt is shaped from hermes_cli/cron.py cron_list at
Hermes v0.22.5: the state badge sits on each job's id line and the Name row is
the first detail row beneath it. Captured output is not a TTY, so it carries no
ANSI codes. The banner and the other detail rows are left out because the
parser never reads them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _brain_watchdog_shared import WATCHDOG

FIXTURE = Path(__file__).parent / "fixtures" / "cron-list-all-v0225.txt"


def _readback_condition() -> str:
    """The `if` condition cron_do() evaluates per seeded job, read from the template."""
    lines = [line for line in WATCHDOG.splitlines() if "grep -B1" in line and "${want}" in line]
    assert len(lines) == 1, lines
    return lines[0].strip().removeprefix("if ").removesuffix("; then")


def _confirmed(job: str, board: str, want: str) -> bool:
    script = (
        'job="$1"; board="$2"; want="$3"; if '
        + _readback_condition()
        + "; then exit 0; fi; exit 1"
    )
    result = subprocess.run(
        ["bash", "-c", script, "bash", job, board, want], check=False
    )
    return result.returncode == 0


def _board() -> str:
    return FIXTURE.read_text()


def test_the_template_condition_is_the_one_under_test() -> None:
    assert _readback_condition() == (
        'grep -B1 -E "^ *Name: +${job}\\$" <<< "${board}" | grep -q "\\[${want}\\]"'
    )


def test_each_name_row_directly_follows_its_badge_line() -> None:
    lines = _board().splitlines()
    name_rows = [i for i, line in enumerate(lines) if line.lstrip().startswith("Name:")]
    assert len(name_rows) == 3
    assert all("[" in lines[i - 1] for i in name_rows)


def test_a_paused_job_is_confirmed_only_as_paused() -> None:
    assert _confirmed("kb-triage", _board(), "paused")
    assert not _confirmed("kb-triage", _board(), "active")


def test_an_active_job_is_confirmed_only_as_active() -> None:
    assert _confirmed("daily-brief", _board(), "active")
    assert not _confirmed("daily-brief", _board(), "paused")


def test_a_disabled_job_is_neither_paused_nor_active() -> None:
    assert not _confirmed("legacy-digest", _board(), "paused")
    assert not _confirmed("legacy-digest", _board(), "active")


def test_a_job_missing_from_the_board_is_not_confirmed() -> None:
    assert not _confirmed("no-such-job", _board(), "paused")


def test_a_name_that_is_a_prefix_of_another_job_does_not_match() -> None:
    assert not _confirmed("daily", _board(), "active")
