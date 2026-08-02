"""The converge-time `hermes memory status` check (verify.yml) used to be pure
telemetry: `debug: var: stdout_lines` prints the same neutral block whether the
command exited 0 or crashed, and nothing downstream reads it — a Hindsight
outage silently degrades memory recall to "no memories" with no operator-visible
signal (see "Memory resilience" in defaults/main.yml, above
hermes_agent_hindsight_client_pin). This proves the replacement debug message
actually renders a distinct HEALTHY/DEGRADED verdict instead of raw text a
human has to interpret themselves, and that it is still never a hard gate.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/verify.yml").read_text()


def _search_test(value: str, pattern: str = "", ignorecase: bool = False, multiline: bool = False) -> bool:
    """Minimal stand-in for ansible.builtin's `search` Jinja test."""
    flags = (re.IGNORECASE if ignorecase else 0) | (re.MULTILINE if multiline else 0)
    return re.search(pattern, value, flags) is not None


def _memory_status_task() -> dict[str, Any]:
    tasks = yaml.safe_load(VERIFY_TASKS)
    for task in tasks:
        if task.get("name") == "Report the active memory provider":
            return task
    raise AssertionError("'Report the active memory provider' task not found in verify.yml")


def _render_verdict(rc: int, stdout: str) -> str:
    env = Environment(autoescape=False)
    env.tests["search"] = _search_test
    msg_template = _memory_status_task()["ansible.builtin.debug"]["msg"]
    return env.from_string(msg_template).render(
        hermes_agent_memory_status={"rc": rc, "stdout": stdout, "stdout_lines": stdout.splitlines()}
    )


def test_probe_stays_non_fatal() -> None:
    # The command that can actually observe a real failure must never fail
    # the converge — this is best-effort telemetry, not a gate (see the block
    # comment at the top of verify.yml explaining why the memory-provider
    # report is deliberately OUTSIDE the gated block).
    tasks = yaml.safe_load(VERIFY_TASKS)
    probe = next(t for t in tasks if t.get("name") == "Check the active memory provider (non-fatal)")
    assert probe["failed_when"] is False


def test_nonzero_exit_reports_degraded() -> None:
    assert "DEGRADED (rc=1)" in _render_verdict(rc=1, stdout="boom")


def test_not_available_string_reports_degraded_even_with_rc_zero() -> None:
    verdict = _render_verdict(rc=0, stdout="Provider: hindsight\nMemory is NOT AVAILABLE")
    assert "DEGRADED" in verdict
    assert "not available" in verdict.lower()


def test_clean_exit_with_no_known_bad_string_reports_healthy() -> None:
    verdict = _render_verdict(rc=0, stdout="Provider: hindsight\nAvailable: true")
    assert "HEALTHY" in verdict
    assert "DEGRADED" not in verdict
