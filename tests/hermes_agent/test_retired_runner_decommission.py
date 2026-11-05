"""Keep retired Codex runner cleanup behind the live no-Codex Hermes config."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from _role_files import include_file

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TASKS = ROLE / "tasks"


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_cleanup_follows_config_render_and_handler_flush() -> None:
    main = yaml.safe_load((TASKS / "main.yml").read_text())
    config = next(
        index for index, task in enumerate(main)
        if (
            include_file(task, "ansible.builtin.import_tasks")
            or include_file(task, "ansible.builtin.include_tasks")
        ) == "config_and_content.yml"
    )
    finalize = next(
        index for index, task in enumerate(main)
        if (
            include_file(task, "ansible.builtin.import_tasks")
            or include_file(task, "ansible.builtin.include_tasks")
        ) == "finalize.yml"
    )
    cleanup = next(
        index for index, task in enumerate(main)
        if task.get("ansible.builtin.import_tasks") == "decommission_retired_runner.yml"
    )
    finalize_tasks = yaml.safe_load((TASKS / "finalize.yml").read_text())
    finalize_names = [task["name"] for task in finalize_tasks]

    assert config < finalize < cleanup
    assert finalize_names.index("Flush handlers so config/unit changes apply before verification") < (
        finalize_names.index("Verify the brain wiring end-to-end after converge")
    )


def test_cleanup_removes_the_three_exact_retired_artifacts() -> None:
    cleanup = yaml.safe_load((TASKS / "decommission_retired_runner.yml").read_text())
    sudoers = _task(cleanup, "Remove the retired codex-runner sudoers grant")
    user = _task(cleanup, "Remove the retired codex-runner user")
    home = _task(cleanup, "Remove the retired codex-runner home")

    assert sudoers["ansible.builtin.file"] == {
        "path": "/etc/sudoers.d/codex-runner-mcp",
        "state": "absent",
    }
    assert user["ansible.builtin.user"] == {
        "name": "codex-runner",
        "state": "absent",
        "remove": True,
    }
    assert home["ansible.builtin.file"] == {
        "path": "/var/lib/codex-runner",
        "state": "absent",
    }


def test_runner_processes_stop_before_the_account_is_removed() -> None:
    cleanup = yaml.safe_load((TASKS / "decommission_retired_runner.yml").read_text())
    names = [task["name"] for task in cleanup]
    account = _task(cleanup, "Look up the retired codex-runner account")
    stop = _task(cleanup, "Stop processes owned by the retired codex-runner account")
    wait = _task(cleanup, "Wait for retired codex-runner processes to stop")

    assert account["ansible.builtin.getent"] == {
        "database": "passwd",
        "key": "codex-runner",
        "fail_key": False,
    }
    assert stop["ansible.builtin.command"]["argv"] == [
        "pkill", "--signal", "TERM", "--uid", "codex-runner"
    ]
    assert stop["changed_when"] == "hermes_agent_retired_runner_process_stop.rc == 0"
    assert stop["failed_when"] == "hermes_agent_retired_runner_process_stop.rc not in [0, 1]"
    assert wait["failed_when"] == "hermes_agent_retired_runner_processes.rc not in [0, 1]"
    assert wait["until"] == "hermes_agent_retired_runner_processes.rc == 1"
    assert stop["when"] == wait["when"]
    assert "hermes_agent_retired_runner_account_lookup.ansible_facts.getent_passwd" in stop["when"]
    assert names.index(stop["name"]) < names.index(wait["name"]) < names.index(
        "Remove the retired codex-runner user"
    )


RUNNER_PROCESS_TASKS = (
    "Look up the retired codex-runner account",
    "Stop processes owned by the retired codex-runner account",
    "Wait for retired codex-runner processes to stop",
)


def _write_stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("account_present", "check_mode", "expected_calls"),
    [
        (False, False, []),
        (True, False, ["pkill --signal TERM --uid codex-runner", "pgrep --uid codex-runner"]),
        (True, True, ["pgrep --uid codex-runner"]),
    ],
    ids=("account-absent", "no-processes", "no-processes-check-mode"),
)
def test_runner_kill_and_wait_accept_no_matching_process(
    tmp_path, account_present, check_mode, expected_calls
) -> None:
    """Run the real lookup, kill and wait tasks against stubs that match no process.

    Only read-only checks and the kill are recorded. An absent account must
    skip the kill and the wait, and in check mode the kill must not run while
    the wait still reads the real process table.
    """
    cleanup = yaml.safe_load((TASKS / "decommission_retired_runner.yml").read_text())
    selected = [_task(cleanup, name) for name in RUNNER_PROCESS_TASKS]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    record = f'echo "$(basename "$0") $*" >> {shlex.quote(str(calls))}'
    if account_present:
        getent = (
            'test "$1 $2" = "passwd codex-runner" || exit 2\n'
            'echo "codex-runner:x:994:994::/var/lib/codex-runner:/usr/sbin/nologin"'
        )
    else:
        getent = "exit 2"
    _write_stub(bin_dir / "getent", getent)
    _write_stub(bin_dir / "pkill", f"{record}\nexit 1")
    _write_stub(bin_dir / "pgrep", f"{record}\nexit 1")

    playbook = tmp_path / "playbook.yml"
    playbook.write_text(
        yaml.safe_dump([{"hosts": "localhost", "connection": "local", "gather_facts": False,
                         "tasks": selected}], sort_keys=False),
        encoding="utf-8",
    )
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        yaml.safe_dump({"all": {"hosts": {"localhost": {
            "ansible_connection": "local",
            "ansible_python_interpreter": sys.executable,
        }}}}),
        encoding="utf-8",
    )
    ansible_config = tmp_path / "ansible.cfg"
    ansible_config.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["ANSIBLE_CONFIG"] = str(ansible_config)
    command = [shutil.which("ansible-playbook"), str(playbook), "-i", str(inventory)]
    if check_mode:
        command.append("--check")
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    assert recorded == expected_calls
