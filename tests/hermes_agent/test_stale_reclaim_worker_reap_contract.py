from __future__ import annotations

import io
import re
import signal as signal_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from _pinned_sources_worker import PINNED_STALE_RECLAIM_TERMINATE_SOURCE
from _role_files import role_tasks


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

# PINNED_STALE_RECLAIM_TERMINATE_SOURCE is upstream v0.22.5 text: _sigkill and
# _terminate_reclaimed_worker, run as written (fingerprint refusal, host-local
# check and UNVERIFIED branch included).

RECLAIM_SIGNAL_SAFETY_PATCH_NAME = (
    "Patch Hermes stale-reclaim worker termination to skip init and self PIDs before signaling"
)
RECLAIM_SIGTERM_PATCH_NAME = (
    "Patch Hermes stale-reclaim worker termination to signal the worker's process group"
)
# Shared with the timeout path (test_worker_reap_contract.py): ONE patch on
# _sigkill()'s own body closes the process-group gap for both reapers, since
# both now call the same helper.
RECLAIM_SIGKILL_PATCH_NAME = (
    "Patch Hermes worker-reap SIGKILL escalation to signal the worker's process group"
)
LEGACY_GUARD_TASK_NAME = "Patch Hermes worker-reap legacy-row identity guard before signaling"
WORKER_CMDLINE = "python -m hermes_cli.kanban_worker_client"


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


def _legacy_helper_block() -> str:
    return _task(LEGACY_GUARD_TASK_NAME)["ansible.builtin.blockinfile"]["block"]


def _patched_reclaim_source() -> str:
    source = PINNED_STALE_RECLAIM_TERMINATE_SOURCE
    for name in (
        RECLAIM_SIGNAL_SAFETY_PATCH_NAME,
        RECLAIM_SIGTERM_PATCH_NAME,
        RECLAIM_SIGKILL_PATCH_NAME,
    ):
        source = _apply_replace(name, source)
    return _legacy_helper_block() + "\n\n" + source


class _FixedPid:
    def __init__(self, pid: int) -> None:
        self._pid = pid

    def getpid(self) -> int:
        return self._pid


class _RecordingLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.lines.append(message % args if args else message)


def _fake_proc_open(cmdline: str):
    def fake_open(path, mode="rb", *args, **kwargs):
        assert path.endswith("/cmdline"), path
        return io.BytesIO(cmdline.encode("utf-8"))

    return fake_open


def _build_reclaim(
    *,
    own_pid: int,
    pid_alive: bool,
    recycled: bool = False,
    cmdline: str = WORKER_CMDLINE,
):
    log = _RecordingLog()
    namespace: dict[str, Any] = {
        "Any": Any,
        "Optional": Optional,
        "os": _FixedPid(own_pid),
        "signal": signal_module,
        "open": _fake_proc_open(cmdline),
        "UNVERIFIED_WORKER_FINGERPRINT": "unverified",
        "_kb": SimpleNamespace(
            _pid_alive=lambda pid: pid_alive,
            _host_prefix=lambda: "host:",
            _log=log,
        ),
        "_pid_recycled": lambda pid, started_at: recycled,
        "_kill_fn": lambda signal_fn: signal_fn,
        "_worker_alive": lambda pid, started_at: pid_alive,
        "_poll_worker_exit": lambda pid, started_at=None: not pid_alive,
    }
    exec(compile(_patched_reclaim_source(), "<patched-reclaim>", "exec"), namespace)
    return namespace["_terminate_reclaimed_worker"], log


def _reclaim(terminate, pid: int, started_at: Any = None) -> tuple[list[tuple[int, int]], Any]:
    calls: list[tuple[int, int]] = []
    info = terminate(
        pid,
        "host:claim",
        signal_fn=lambda target, sig: calls.append((target, sig)),
        started_at=started_at,
    )
    return calls, info


def test_stale_reclaim_reuses_the_shared_identity_helper_from_the_timeout_path() -> None:
    # Both enforce_max_runtime (timeout) and detect_stale_running (stale
    # heartbeat) reap a host-local worker for different reasons but must not
    # diverge in how they do it — a single shared helper, not two.
    cfg = _task(LEGACY_GUARD_TASK_NAME)["ansible.builtin.blockinfile"]
    assert cfg["insertbefore"] == r"^def enforce_max_runtime\("
    assert "def _legacy_worker_may_be_signalled(pid, started_at) -> bool:" in cfg["block"]


def test_stale_reclaim_signals_the_process_group_not_just_the_pid() -> None:
    terminate, _ = _build_reclaim(own_pid=1, pid_alive=False)
    calls, info = _reclaim(terminate, 12345, started_at="fingerprint")
    assert calls == [(-12345, signal_module.SIGTERM)]
    assert info["terminated"] is True
    assert info["sigkill"] is False


def test_stale_reclaim_sigkill_escalation_is_process_group_scoped() -> None:
    # The shared _sigkill(kill, pid) helper both reapers now call through
    # (test_worker_reap_contract.py patches the same body) closes the
    # process-group gap for the stale-reclaim path too, in the same patch.
    terminate, _ = _build_reclaim(own_pid=1, pid_alive=True)
    calls, info = _reclaim(terminate, 12345, started_at="fingerprint")
    assert calls == [
        (-12345, signal_module.SIGTERM),
        (-12345, signal_module.SIGKILL),
    ]
    assert info["sigkill"] is True


def test_stale_reclaim_refuses_to_signal_the_gateways_own_pid() -> None:
    terminate, _ = _build_reclaim(own_pid=12345, pid_alive=True)
    calls, info = _reclaim(terminate, 12345, started_at="fingerprint")
    assert calls == []
    assert info["termination_attempted"] is False


def test_stale_reclaim_refuses_pid_zero_negative_or_init() -> None:
    for unsafe_pid in (0, -5, 1):
        terminate, _ = _build_reclaim(own_pid=99999, pid_alive=True)
        calls, _ = _reclaim(terminate, unsafe_pid, started_at="fingerprint")
        assert calls == [], f"signaled unsafe pid {unsafe_pid}"


def test_stale_reclaim_refuses_a_pid_the_os_recycled_for_something_else() -> None:
    # Upstream's fingerprint refusal, not the role's: a live PID whose spawn
    # fingerprint no longer matches is reported as recycled and never signalled.
    terminate, _ = _build_reclaim(own_pid=1, pid_alive=True, recycled=True)
    calls, info = _reclaim(terminate, 12345, started_at="fingerprint")
    assert calls == []
    assert info["pid_recycled"] is True


def test_stale_reclaim_never_signals_an_unverified_live_worker_by_bare_number() -> None:
    terminate, _ = _build_reclaim(own_pid=1, pid_alive=True)
    calls, info = _reclaim(terminate, 12345, started_at="unverified")
    assert calls == []
    assert info["signal_refused"] is True
    assert info["terminated"] is False


def test_stale_reclaim_treats_a_dead_unverified_pid_as_gone() -> None:
    terminate, _ = _build_reclaim(own_pid=1, pid_alive=False)
    calls, info = _reclaim(terminate, 12345, started_at="unverified")
    assert calls == []
    assert info["terminated"] is True


def test_legacy_row_with_a_worker_command_line_is_signalled_on_reclaim() -> None:
    terminate, log = _build_reclaim(own_pid=1, pid_alive=False)
    calls, info = _reclaim(terminate, 12345, started_at=None)
    assert calls == [(-12345, signal_module.SIGTERM)]
    assert info["terminated"] is True
    assert log.lines == []


def test_legacy_row_whose_pid_is_not_a_kanban_worker_is_skipped_and_logged_once() -> None:
    terminate, log = _build_reclaim(
        own_pid=1, pid_alive=True, cmdline="/usr/bin/some-other-service --serve"
    )
    calls, info = _reclaim(terminate, 12345, started_at=None)
    assert calls == []
    assert info["termination_attempted"] is False
    assert len(log.lines) == 1
    assert "not signalled" in log.lines[0]
