from __future__ import annotations

import contextlib
import io
import re
import signal as signal_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from _pinned_sources_worker import PINNED_WORKER_REAP_SOURCE
from _role_files import role_tasks


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

# PINNED_WORKER_REAP_SOURCE is upstream v0.22.5 text: _sigkill plus the kill
# block of enforce_max_runtime, the lines the role's replace tasks patch. The
# def/for header around the kill block is scaffolding only.

REAP_SIGNAL_SAFETY_PATCH_NAME = (
    "Patch Hermes worker-reap timeout path to skip init and self PIDs before signaling"
)
REAP_SIGTERM_PATCH_NAME = (
    "Patch Hermes worker-reap timeout path to signal the worker's process group"
)
REAP_SIGKILL_PATCH_NAME = (
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


def _patched_reap_source() -> str:
    source = PINNED_WORKER_REAP_SOURCE
    for name in (
        REAP_SIGNAL_SAFETY_PATCH_NAME,
        REAP_SIGTERM_PATCH_NAME,
        REAP_SIGKILL_PATCH_NAME,
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


def _build_enforce(
    *,
    own_pid: int,
    pid_alive: bool,
    recycled: bool = False,
    cmdline: str = WORKER_CMDLINE,
):
    log = _RecordingLog()
    namespace: dict[str, Any] = {
        "os": _FixedPid(own_pid),
        "signal": signal_module,
        "contextlib": contextlib,
        "open": _fake_proc_open(cmdline),
        "_kb": SimpleNamespace(_pid_alive=lambda pid: pid_alive, _log=log),
        "_pid_recycled": lambda pid, started_at: recycled,
        "_kill_fn": lambda signal_fn: signal_fn,
        "_worker_alive": lambda pid, started_at: pid_alive,
        "_poll_worker_exit": lambda pid, started_at=None: not pid_alive,
    }
    exec(compile(_patched_reap_source(), "<patched-reap>", "exec"), namespace)
    return namespace["_enforce_timeout_kill"], log


def _run(reap, pid: int, started_at: Any) -> tuple[list[tuple[int, int]], Any]:
    calls: list[tuple[int, int]] = []
    killed = reap(
        [pid],
        started_at,
        signal_fn=lambda target, sig: calls.append((target, sig)),
    )
    return calls, killed


def test_worker_reap_helper_landed_before_the_function_it_guards() -> None:
    cfg = _task(LEGACY_GUARD_TASK_NAME)["ansible.builtin.blockinfile"]
    assert cfg["insertbefore"] == r"^def enforce_max_runtime\("
    block = cfg["block"]
    assert "def _legacy_worker_may_be_signalled(pid, started_at) -> bool:" in block
    assert '"kanban_worker_client" in cmdline' in block


def test_reap_signals_the_process_group_not_just_the_pid() -> None:
    # A worker's children (start_new_session=True makes it its own process
    # group leader) are the actual leak; signaling only the PID misses them.
    reap, _ = _build_enforce(own_pid=1, pid_alive=False)
    calls, killed = _run(reap, 12345, "fingerprint")
    assert calls == [(-12345, signal_module.SIGTERM)]
    assert killed is False  # SIGTERM alone was enough


def test_reap_escalates_to_sigkill_on_the_process_group_if_sigterm_survives() -> None:
    reap, _ = _build_enforce(own_pid=1, pid_alive=True)
    calls, killed = _run(reap, 12345, "fingerprint")
    assert calls == [
        (-12345, signal_module.SIGTERM),
        (-12345, signal_module.SIGKILL),
    ]
    assert killed is True


def test_reap_refuses_to_signal_the_gateways_own_pid() -> None:
    reap, _ = _build_enforce(own_pid=12345, pid_alive=True)
    calls, _ = _run(reap, 12345, "fingerprint")
    assert calls == []


def test_reap_refuses_pid_zero_negative_or_init() -> None:
    for unsafe_pid in (0, -5, 1):
        reap, _ = _build_enforce(own_pid=99999, pid_alive=True)
        calls, _ = _run(reap, unsafe_pid, "fingerprint")
        assert calls == [], f"signaled unsafe pid {unsafe_pid}"


def test_reap_refuses_a_pid_the_os_recycled_for_something_else() -> None:
    reap, _ = _build_enforce(own_pid=1, pid_alive=True, recycled=True)
    calls, _ = _run(reap, 12345, "fingerprint")
    assert calls == []


def test_fingerprinted_row_is_not_subject_to_the_legacy_command_line_check() -> None:
    reap, log = _build_enforce(
        own_pid=1, pid_alive=False, cmdline="/usr/bin/some-other-service --serve"
    )
    calls, _ = _run(reap, 12345, "fingerprint")
    assert calls == [(-12345, signal_module.SIGTERM)]
    assert log.lines == []


def test_legacy_row_with_a_worker_command_line_is_signalled() -> None:
    reap, log = _build_enforce(own_pid=1, pid_alive=True)
    calls, _ = _run(reap, 12345, None)
    assert calls[0] == (-12345, signal_module.SIGTERM)
    assert log.lines == []


def test_legacy_row_whose_pid_is_not_a_kanban_worker_is_skipped_and_logged_once() -> None:
    reap, log = _build_enforce(
        own_pid=1, pid_alive=True, cmdline="/usr/bin/some-other-service --serve"
    )
    calls, killed = _run(reap, 12345, None)
    assert calls == []
    assert killed is False
    assert len(log.lines) == 1
    assert "not signalled" in log.lines[0]
