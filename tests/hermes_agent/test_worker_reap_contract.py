from __future__ import annotations

import re
import signal as signal_module
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"

# Reduced reap slice from enforce_max_runtime (upstream v2026.7.7.2,
# hermes_cli/kanban_db.py) — the exact lines the role's own regexes target,
# indentation included. Not the whole function: only the SIGTERM/SIGKILL
# block the three replace tasks below patch.
PINNED_REAP_SOURCE = '''\
def _reap(pid, signal_fn=None):
    killed = False
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is not None:
        try:
            kill(pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        for _ in range(10):
            if not _pid_alive(pid):
                break
            time.sleep(0.5)
        if _pid_alive(pid):
            try:
                _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
                kill(pid, _sigkill)
                killed = True
            except (ProcessLookupError, OSError):
                pass
    return killed
'''

REAP_SIGNAL_SAFETY_PATCH_NAME = (
    "Patch Hermes worker-reap timeout path to verify PID safety before signaling"
)
REAP_SIGTERM_PATCH_NAME = (
    "Patch Hermes worker-reap timeout path to signal the worker's process group"
)
REAP_SIGKILL_PATCH_NAME = (
    "Patch Hermes worker-reap timeout path to escalate on the worker's process group"
)


def _task(name: str) -> dict[str, Any]:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "main.yml").read_text())
    return next(item for item in tasks if item.get("name") == name)


def _apply_replace(name: str, source: str) -> str:
    config = _task(name)["ansible.builtin.replace"]
    patched, count = re.subn(
        config["regexp"], config["replace"], source, flags=re.MULTILINE
    )
    assert count == 1, f"{name}: expected exactly one match"
    return patched


def _patched_reap_source() -> str:
    source = PINNED_REAP_SOURCE
    for name in (
        REAP_SIGNAL_SAFETY_PATCH_NAME,
        REAP_SIGTERM_PATCH_NAME,
        REAP_SIGKILL_PATCH_NAME,
    ):
        source = _apply_replace(name, source)
    return source


class _NoSleepTime:
    def sleep(self, seconds: float) -> None:
        pass


class _FixedPid:
    def __init__(self, pid: int) -> None:
        self._pid = pid

    def getpid(self) -> int:
        return self._pid


def _run_patched_reap(source: str, namespace: dict[str, Any]) -> None:
    # Equivalent to exec(source, namespace): compile in "exec" mode, then
    # evaluate the resulting code object against the namespace.
    code = compile(source, "<patched-reap>", "exec")
    eval(code, namespace)  # noqa: S307 -- test-only, self-authored source


def _build_reap(*, own_pid: int, pid_alive: bool, is_hermes_worker: bool):
    namespace: dict[str, Any] = {
        "os": _FixedPid(own_pid),
        "signal": signal_module,
        "time": _NoSleepTime(),
        "_pid_alive": lambda pid: pid_alive,
        "_pid_is_hermes_worker": lambda pid: is_hermes_worker,
    }
    _run_patched_reap(_patched_reap_source(), namespace)
    return namespace["_reap"]


def test_worker_reap_helper_landed_before_the_function_it_guards() -> None:
    task = _task(
        "Patch Hermes worker-reap helper to verify PID identity before signaling"
    )
    cfg = task["ansible.builtin.blockinfile"]
    assert cfg["insertbefore"] == r"^def enforce_max_runtime\("
    block = cfg["block"]
    assert "def _pid_is_hermes_worker(pid: int) -> bool:" in block
    assert '"hermes" in cmdline and "chat" in cmdline' in block


def test_reap_signals_the_process_group_not_just_the_pid() -> None:
    # A worker's children (start_new_session=True makes it its own process
    # group leader) are the actual leak; signaling only the PID misses them.
    reap = _build_reap(own_pid=1, pid_alive=False, is_hermes_worker=True)
    calls: list[tuple[int, int]] = []
    killed = reap(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == [(-12345, signal_module.SIGTERM)]
    assert killed is False  # SIGTERM alone was enough


def test_reap_escalates_to_sigkill_on_the_process_group_if_sigterm_survives() -> None:
    reap = _build_reap(own_pid=1, pid_alive=True, is_hermes_worker=True)
    calls: list[tuple[int, int]] = []
    killed = reap(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == [
        (-12345, signal_module.SIGTERM),
        (-12345, signal_module.SIGKILL),
    ]
    assert killed is True


def test_reap_refuses_to_signal_the_gateways_own_pid() -> None:
    reap = _build_reap(own_pid=12345, pid_alive=True, is_hermes_worker=True)
    calls: list[tuple[int, int]] = []
    reap(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == []


def test_reap_refuses_pid_zero_negative_or_init() -> None:
    for unsafe_pid in (0, -5, 1):
        reap = _build_reap(own_pid=99999, pid_alive=True, is_hermes_worker=True)
        calls: list[tuple[int, int]] = []
        reap(unsafe_pid, signal_fn=lambda pid, sig: calls.append((pid, sig)))
        assert calls == [], f"signaled unsafe pid {unsafe_pid}"


def test_reap_refuses_a_pid_the_os_recycled_for_something_else() -> None:
    reap = _build_reap(own_pid=1, pid_alive=True, is_hermes_worker=False)
    calls: list[tuple[int, int]] = []
    reap(12345, signal_fn=lambda pid, sig: calls.append((pid, sig)))
    assert calls == []
