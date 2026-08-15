from __future__ import annotations

import importlib.util
import os
import re
import signal as signal_module
import tempfile
from pathlib import Path
from typing import Any

import yaml
from _role_files import role_tasks


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

# Reduced _terminate_reclaimed_worker slice from detect_stale_running's
# reclaim path (upstream v2026.7.7.2, hermes_cli/kanban_db.py) — the exact
# lines the role's own regexes target, indentation included. Starts after
# the pid/claim_lock and host-local checks, which the patches here don't
# touch.
PINNED_TERMINATE_SOURCE = '''\
def _reclaim(pid, signal_fn=None):
    info = {"terminated": False, "sigkill": False}
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is None:
        return info

    info["termination_attempted"] = True
    try:
        kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        info["terminated"] = True
        return info
    except OSError:
        return info

    for _ in range(10):
        if not _pid_alive(pid):
            info["terminated"] = True
            return info
        time.sleep(0.5)

    if _pid_alive(pid):
        try:
            _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            kill(int(pid), _sigkill)
            info["sigkill"] = True
        except (ProcessLookupError, OSError):
            return info

    info["terminated"] = not _pid_alive(pid)
    return info
'''

RECLAIM_SIGNAL_SAFETY_PATCH_NAME = (
    "Patch Hermes stale-reclaim worker termination to verify PID safety before signaling"
)
RECLAIM_SIGTERM_PATCH_NAME = (
    "Patch Hermes stale-reclaim worker termination to signal the worker's process group"
)
RECLAIM_SIGKILL_PATCH_NAME = (
    "Patch Hermes stale-reclaim worker termination to escalate on the worker's process group"
)


def _task(name: str) -> dict[str, Any]:
    tasks = role_tasks(ROLE_ROOT)
    return next(item for item in tasks if item.get("name") == name)


def _apply_replace(name: str, source: str) -> str:
    config = _task(name)["ansible.builtin.replace"]
    patched, count = re.subn(
        config["regexp"], config["replace"], source, flags=re.MULTILINE
    )
    assert count == 1, f"{name}: expected exactly one match"
    return patched


def _patched_reclaim_source() -> str:
    source = PINNED_TERMINATE_SOURCE
    for name in (
        RECLAIM_SIGNAL_SAFETY_PATCH_NAME,
        RECLAIM_SIGTERM_PATCH_NAME,
        RECLAIM_SIGKILL_PATCH_NAME,
    ):
        source = _apply_replace(name, source)
    return source


class _NoSleepTime:
    def sleep(self, _seconds: float) -> None:
        pass


class _FixedPid:
    def __init__(self, pid: int) -> None:
        self._pid = pid

    def getpid(self) -> int:
        return self._pid


def _load_patched_reclaim(source: str, *, own_pid: int, pid_alive: bool, is_hermes_worker: bool):
    # Load the patched source as a real module rather than running it
    # in-process, so the function's free variables (os, signal, time,
    # _pid_alive, _pid_is_hermes_worker) resolve from a controlled
    # namespace we fully own.
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        handle.write(source)
        handle.close()
        spec = importlib.util.spec_from_file_location("_patched_reclaim", handle.name)
        assert spec is not None and spec.loader is not None, (
            "patched reclaim fragment did not resolve to a loadable module"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.unlink(handle.name)

    setattr(module, "os", _FixedPid(own_pid))
    setattr(module, "signal", signal_module)
    setattr(module, "time", _NoSleepTime())
    setattr(module, "_pid_alive", lambda _pid: pid_alive)
    setattr(module, "_pid_is_hermes_worker", lambda _pid: is_hermes_worker)
    return module._reclaim


def test_stale_reclaim_reuses_the_shared_identity_helper_from_the_timeout_path() -> None:
    # Both enforce_max_runtime (timeout) and detect_stale_running (stale
    # heartbeat) reap a host-local worker for different reasons but must not
    # diverge in how they do it — a single shared helper, not two.
    helper_task = _task(
        "Patch Hermes worker-reap helper to verify PID identity before signaling"
    )
    cfg = helper_task["ansible.builtin.blockinfile"]
    assert cfg["insertbefore"] == r"^def enforce_max_runtime\("
    assert "def _pid_is_hermes_worker(pid: int) -> bool:" in cfg["block"]


def test_stale_reclaim_signals_the_process_group_not_just_the_pid() -> None:
    reclaim = _load_patched_reclaim(
        _patched_reclaim_source(), own_pid=1, pid_alive=False, is_hermes_worker=True
    )
    calls: list[tuple[int, int]] = []
    info = reclaim(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == [(-12345, signal_module.SIGTERM)]
    assert info["terminated"] is True
    assert info["sigkill"] is False


def test_stale_reclaim_escalates_to_sigkill_on_the_process_group_if_sigterm_survives() -> None:
    reclaim = _load_patched_reclaim(
        _patched_reclaim_source(), own_pid=1, pid_alive=True, is_hermes_worker=True
    )
    calls: list[tuple[int, int]] = []
    reclaim(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == [
        (-12345, signal_module.SIGTERM),
        (-12345, signal_module.SIGKILL),
    ]


def test_stale_reclaim_refuses_to_signal_the_gateways_own_pid() -> None:
    reclaim = _load_patched_reclaim(
        _patched_reclaim_source(), own_pid=12345, pid_alive=True, is_hermes_worker=True
    )
    calls: list[tuple[int, int]] = []
    reclaim(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == []


def test_stale_reclaim_refuses_pid_zero_negative_or_init() -> None:
    for unsafe_pid in (0, -5, 1):
        reclaim = _load_patched_reclaim(
            _patched_reclaim_source(), own_pid=99999, pid_alive=True, is_hermes_worker=True
        )
        calls: list[tuple[int, int]] = []
        reclaim(unsafe_pid, signal_fn=lambda pid, sig: calls.append((pid, sig)))
        assert calls == [], f"signaled unsafe pid {unsafe_pid}"


def test_stale_reclaim_refuses_a_pid_the_os_recycled_for_something_else() -> None:
    reclaim = _load_patched_reclaim(
        _patched_reclaim_source(), own_pid=1, pid_alive=True, is_hermes_worker=False
    )
    calls: list[tuple[int, int]] = []
    reclaim(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == []
