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
from _cron_pool_ceiling_shared import wall_timeout_seconds
from _role_files import template_text


def _helper_namespace() -> dict:
    block = _task("Add aggregate cron wall-clock helpers")[
        "ansible.builtin.blockinfile"
    ]["block"]
    # hermes_agent_cron_wall_timeout_seconds is a derived Jinja formula now,
    # not a literal (defaults/main/20-brain-and-slack.yml) — render it for
    # real rather than reading the raw template string out of role_defaults().
    block = Environment(autoescape=False).from_string(block).render(
        hermes_agent_cron_wall_timeout_seconds=wall_timeout_seconds()
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

    def done(self) -> bool:
        return self.clock.now >= self.done_at


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


class _FakeWatchStop:
    """Fake threading.Event: real code only calls .set()/.wait() on it; the
    watchdog loop below is single-shot and ticked by hand, so .wait()'s
    return value is never consulted."""

    def __init__(self) -> None:
        self.is_set_ = False

    def set(self) -> None:
        self.is_set_ = True

    def wait(self, _timeout: float | None = None) -> bool:
        return self.is_set_


class _FakeWatchThread:
    """Fake threading.Thread: upstream's real background watchdog thread is
    unfakeable without either real wall-clock waits (slow, flaky) or a
    cooperative simulation -- this is the latter. `.start()` does not run the
    target; instead every fake `concurrent.futures.wait()` tick (below) also
    ticks every started fake thread once, interleaving the "background"
    watchdog check with the main poll loop deterministically, in lockstep
    with the same fake clock, with no real concurrency at all."""

    _registry: list["_FakeWatchThread"] = []

    def __init__(self, target=None, name=None, daemon=None) -> None:
        self.target = target
        self.started = False

    def start(self) -> None:
        self.started = True
        type(self)._registry.append(self)


def _compiled_monitor(clock: _Clock, future: _Future, interrupts: list[str]):
    namespace = _helper_namespace()
    _FakeWatchThread._registry = []

    def wait(futures, timeout):
        selected = next(iter(futures))
        clock.now = min(clock.now + timeout, selected.done_at)
        done = clock.now >= selected.done_at
        # Give every started fake watchdog thread one check per main-loop
        # poll tick -- see _FakeWatchThread.
        for watch_thread in _FakeWatchThread._registry:
            if watch_thread.started:
                watch_thread.target()
        if done:
            return {selected}, set()
        return set(), set(futures)

    def _inactivity_watchdog_loop(*, get_idle_seconds, limit_s, poll_s, stop, future_done):
        # One-shot: called once per tick from the fake `wait()` above,
        # rather than looping internally on a real threading.Event.wait().
        if future_done():
            return False
        return float(get_idle_seconds() or 0.0) >= limit_s

    def _cron_inactivity_seconds() -> float:
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        if not raw:
            return 600.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 600.0

    def _raise_inactivity_timeout(agent, job_name, limit_s) -> None:
        _activity: dict = {}
        if hasattr(agent, "get_activity_summary"):
            try:
                _activity = agent.get_activity_summary()
            except Exception:
                pass
        _last_desc = _activity.get("last_activity_desc", "unknown")
        _secs_ago = _activity.get("seconds_since_activity", 0)
        namespace["request_hard_interrupt"](agent, "Cron job timed out (inactivity)")
        raise TimeoutError(
            f"Cron job '{job_name}' idle for "
            f"{int(_secs_ago)}s (limit {int(limit_s)}s) "
            f"— last activity: {_last_desc}"
        )

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
            "threading": SimpleNamespace(Event=_FakeWatchStop, Thread=_FakeWatchThread),
            "heartbeat_run_claim": lambda *_args, **_kwargs: None,
            "_hermes_cron_goal_run": lambda *_args, **_kwargs: None,
            "request_hard_interrupt": lambda _agent, reason: interrupts.append(reason),
            "_RUN_CLAIM_HEARTBEAT_SECONDS": 30.0,
            "_cron_inactivity_seconds": _cron_inactivity_seconds,
            "_inactivity_watchdog_loop": _inactivity_watchdog_loop,
            "_raise_inactivity_timeout": _raise_inactivity_timeout,
            # A local of the enclosing scheduler function on the guest.
            "cron_session_id": "cron_test_session",
        }
    )
    source = (
        "def run_monitor(agent, prompt, job, job_id, job_name, cancel_event=None, "
        "worker_state=None):\n" + PATCHED_CRON_TIMEOUT_SOURCE
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
    managed = float(wall_timeout_seconds())
    limit = _helper_namespace()["_hermes_cron_wall_timeout_limit"]
    monkeypatch.delenv("HERMES_CRON_WALL_TIMEOUT", raising=False)
    assert limit() == managed
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "1800")
    assert limit() == 1800.0
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "0")
    assert limit() is None
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "invalid")
    assert limit() == managed


def test_native_inactivity_and_aggregate_deadlines_are_distinct() -> None:
    defaults = role_defaults(ROLE_ROOT)
    environment = template_text(ROLE_ROOT, "hermes-env.j2")
    assert defaults["hermes_agent_cron_inactivity_timeout_seconds"] == 1800
    # hermes_agent_cron_wall_timeout_seconds is now a derived formula
    # (test_cron_pool_ceiling.py covers it), not a literal — check here only
    # that it stays a genuinely independent knob: its own template text
    # never references the inactivity timeout, so neither can silently
    # collapse into the other.
    assert wall_timeout_seconds() > 0
    assert (
        "hermes_agent_cron_inactivity_timeout_seconds"
        not in defaults["hermes_agent_cron_wall_timeout_seconds"]
    )
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
    # The inactivity-comparison guard patch that used to sit here is retired
    # (patches_cron_wall_clock.yml): its regexp no longer matches 2026.9.11's
    # restructured inactivity poll, so it is a no-op and this fixture's
    # unguarded "if _idle_secs >= _cron_inactivity_limit:" line is left as
    # upstream wrote it. Upstream's own structural None-guard, which lives
    # in _watch_inactivity outside this fixture's narrower snippet, is
    # asserted against the real installed file in
    # patches_verify_cron_wall_clock.yml, not here.


def test_active_run_executes_the_integrated_hard_wall(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "3")
    monkeypatch.setenv("HERMES_CRON_TIMEOUT", "2")
    clock = _Clock()
    future = _Future(clock, done_at=30.0)
    interrupts: list[str] = []
    monitor = _compiled_monitor(clock, future, interrupts)

    with pytest.raises(TimeoutError, match="aggregate wall clock 3s") as raised:
        monitor(_Agent(clock, active=True), "prompt", {}, "job-id", "job")

    assert clock.now == 3.0
    assert interrupts == ["Cron job exceeded hard wall clock"]
    # Vikunja 1923: the kill records what the run did, not only that it died.
    message = str(raised.value)
    assert "after 1 API call(s)" in message
    assert "last activity 0s before the kill: synthetic activity" in message
    assert "session cron_test_session" in message


def test_inactivity_executes_before_the_integrated_hard_wall(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "30")
    monkeypatch.setenv("HERMES_CRON_TIMEOUT", "2")
    clock = _Clock()
    future = _Future(clock, done_at=60.0)
    interrupts: list[str] = []
    monitor = _compiled_monitor(clock, future, interrupts)

    with pytest.raises(TimeoutError, match="idle for 5s"):
        monitor(_Agent(clock, active=False), "prompt", {}, "job-id", "job")

    assert clock.now == 5.0
    assert interrupts == ["Cron job timed out (inactivity)"]


def test_integrated_monitor_returns_a_completed_future(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CRON_WALL_TIMEOUT", "30")
    monkeypatch.setenv("HERMES_CRON_TIMEOUT", "2")
    clock = _Clock()
    future = _Future(clock, done_at=1.0)
    interrupts: list[str] = []
    monitor = _compiled_monitor(clock, future, interrupts)

    result = monitor(_Agent(clock, active=True), "prompt", {}, "job-id", "job")

    assert result == {"completed": True, "final_response": "ok"}
    assert clock.now == 1.0
    assert interrupts == []


def test_wall_clock_patch_runs_before_source_verification() -> None:
    main = (ROLE_ROOT / "tasks" / "main.yml").read_text()
    assert main.index("patches_cron_wall_clock.yml") < main.index(
        "patches_verify.yml"
    )
