"""Aggregate cron deadline, separate from upstream's inactivity reset."""

from __future__ import annotations

import logging
import os
import time
from types import SimpleNamespace

import pytest
from jinja2 import Environment

from conftest import (
    PATCHED_CRON_TIMEOUT_SOURCE,
    ROLE_ROOT,
    _task,
    role_defaults,
)


def _helper_namespace() -> dict:
    block = _task("Add aggregate cron wall-clock helpers")[
        "ansible.builtin.blockinfile"
    ]["block"]
    defaults = role_defaults(ROLE_ROOT)
    block = Environment(autoescape=False).from_string(block).render(
        hermes_agent_cron_wall_timeout_seconds=defaults[
            "hermes_agent_cron_wall_timeout_seconds"
        ]
    )
    namespace = {"logger": logging.getLogger(__name__), "os": os, "time": time}
    exec(block, namespace)  # noqa: S102 - executes the role's exact managed Python
    return namespace


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _Future:
    def __init__(self, clock: _Clock, done_at: float) -> None:
        self.clock = clock
        self.done_at = done_at

    def result(self) -> dict:
        return {"completed": True, "final_response": "ok"}


class _Executor:
    def __init__(self, future: _Future, **_kwargs) -> None:
        self.future = future
        self.shutdown_calls = 0

    def submit(self, *_args, **_kwargs) -> _Future:
        return self.future

    def shutdown(self, **_kwargs) -> None:
        self.shutdown_calls += 1


class _Agent:
    def __init__(self, clock: _Clock, active: bool) -> None:
        self.clock = clock
        self.active = active

    def run_conversation(self, _prompt: str) -> dict:
        raise AssertionError("the fake executor must own future completion")

    def get_activity_summary(self) -> dict:
        idle = 0.0 if self.active else self.clock.now
        return {
            "seconds_since_activity": idle,
            "last_activity_desc": "synthetic activity",
            "api_call_count": 1,
            "max_iterations": 10,
            "current_tool": None,
        }


def _compiled_monitor(clock: _Clock, future: _Future, interrupts: list[str]):
    namespace = _helper_namespace()

    def wait(futures, timeout):
        selected = next(iter(futures))
        clock.now = min(clock.now + timeout, selected.done_at)
        if clock.now >= selected.done_at:
            return {selected}, set()
        return set(), set(futures)

    namespace.update(
        {
            "time": clock,
            "concurrent": SimpleNamespace(
                futures=SimpleNamespace(
                    ThreadPoolExecutor=lambda **kwargs: _Executor(future, **kwargs),
                    wait=wait,
                )
            ),
            "contextvars": SimpleNamespace(
                copy_context=lambda: SimpleNamespace(run=lambda fn, *args: fn(*args))
            ),
            "heartbeat_run_claim": lambda *_args, **_kwargs: None,
            "_hermes_cron_goal_run": lambda *_args, **_kwargs: None,
            "request_hard_interrupt": lambda _agent, reason: interrupts.append(reason),
            "_RUN_CLAIM_HEARTBEAT_SECONDS": 30.0,
        }
    )
    source = (
        "def run_monitor(agent, prompt, job, job_id, job_name, _cron_timeout):\n"
        + PATCHED_CRON_TIMEOUT_SOURCE
        + "        return result\n"
    )
    exec(compile(source, "<patched-cron-monitor>", "exec"), namespace)
    return namespace["run_monitor"]


def test_wall_clock_boundary_does_not_reset_on_activity() -> None:
    helpers = _helper_namespace()
    expired = helpers["_hermes_cron_wall_clock_expired"]
    poll_timeout = helpers["_hermes_cron_poll_timeout"]
    assert expired(100.0, 1800.0, now=1899.999) is False
    assert expired(100.0, 1800.0, now=1900.0) is True
    assert expired(100.0, None, now=999999.0) is False
    assert poll_timeout(100.0, 1800.0, 5.0, now=1899.25) == 0.75
    assert poll_timeout(100.0, 1800.0, 5.0, now=1900.0) == 0.0
    assert poll_timeout(100.0, None, 5.0, now=999999.0) == 5.0


def test_wall_clock_limit_parses_one_process_environment(monkeypatch) -> None:
    limit = _helper_namespace()["_hermes_cron_wall_timeout_limit"]
    monkeypatch.delenv("HERMES_CRON_WALL_TIMEOUT", raising=False)
    assert limit() == 2300.0
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "1800")
    assert limit() == 1800.0
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "0")
    assert limit() is None
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "invalid")
    assert limit() == 2300.0


def test_native_inactivity_and_aggregate_deadlines_are_distinct() -> None:
    defaults = role_defaults(ROLE_ROOT)
    environment = (ROLE_ROOT / "templates" / "hermes-env.j2").read_text()
    assert defaults["hermes_agent_cron_inactivity_timeout_seconds"] == 1800
    assert defaults["hermes_agent_cron_wall_timeout_seconds"] == 2300
    assert (
        "HERMES_CRON_TIMEOUT={{ hermes_agent_cron_inactivity_timeout_seconds }}"
        in environment
    )
    assert (
        "HERMES_CRON_WALL_TIMEOUT={{ hermes_agent_cron_wall_timeout_seconds }}"
        in environment
    )


def test_patched_monitor_checks_the_hard_wall_and_preserves_idle_guard() -> None:
    source = PATCHED_CRON_TIMEOUT_SOURCE
    assert "_cron_started_monotonic = time.monotonic()" in source
    assert source.index("_cron_started_monotonic") < source.index("_cron_future =")
    assert "_cron_wait_timeout = _hermes_cron_poll_timeout(" in source
    assert "timeout=_cron_wait_timeout" in source
    assert "_hermes_cron_wall_clock_expired(" in source
    assert "_hard_timeout = True" in source
    assert "Cron job exceeded hard wall clock" in source
    assert (
        "if _cron_inactivity_limit is not None and _idle_secs >= "
        "_cron_inactivity_limit:" in source
    )
    assert "if _idle_secs >= _cron_inactivity_limit:" not in source


def test_active_run_executes_the_integrated_hard_wall(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "3")
    clock = _Clock()
    future = _Future(clock, done_at=30.0)
    interrupts: list[str] = []
    monitor = _compiled_monitor(clock, future, interrupts)

    with pytest.raises(TimeoutError, match="aggregate wall clock 3s"):
        monitor(_Agent(clock, active=True), "prompt", {}, "job-id", "job", 2.0)

    assert clock.now == 3.0
    assert interrupts == ["Cron job exceeded hard wall clock"]


def test_inactivity_executes_before_the_integrated_hard_wall(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "30")
    clock = _Clock()
    future = _Future(clock, done_at=60.0)
    interrupts: list[str] = []
    monitor = _compiled_monitor(clock, future, interrupts)

    with pytest.raises(TimeoutError, match="idle for 5s"):
        monitor(_Agent(clock, active=False), "prompt", {}, "job-id", "job", 2.0)

    assert clock.now == 5.0
    assert interrupts == ["Cron job timed out (inactivity)"]


def test_integrated_monitor_returns_a_completed_future(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "30")
    clock = _Clock()
    future = _Future(clock, done_at=1.0)
    interrupts: list[str] = []
    monitor = _compiled_monitor(clock, future, interrupts)

    result = monitor(_Agent(clock, active=True), "prompt", {}, "job-id", "job", 2.0)

    assert result == {"completed": True, "final_response": "ok"}
    assert clock.now == 1.0
    assert interrupts == []


def test_wall_clock_patch_runs_before_source_verification() -> None:
    main = (ROLE_ROOT / "tasks" / "main.yml").read_text()
    assert main.index("patches_cron_wall_clock.yml") < main.index(
        "patches_verify.yml"
    )
