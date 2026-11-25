"""agent_exec stays check-mode safe: preview tasks preview, gated tasks gate.

Check mode cannot start the agent-exec service or create its venv, so the two
tasks that depend on that state must skip there — the same gate the hermes_agent
verify uses ("not ansible_check_mode"). Everything else in the role must keep
previewing: a blanket role-wide skip would make --check useless for the platform
tasks. The behavioral tests below run the role's real verify task through
ansible-playbook so removing the gate fails loudly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_MAIN = REPO_ROOT / "roles/agent_exec/tasks/main.yml"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)

SYSTEMCTL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$AGENT_EXEC_SYSTEMCTL_LOG"
if [ "$1" = "--version" ]; then
  printf 'systemd 252\\n'
elif [ "$1" = "show" ]; then
  printf 'Id=agent-exec.service\\nLoadState=loaded\\nActiveState=%s\\nSubState=%s\\n' \\
    "$AGENT_EXEC_ACTIVE_STATE" "$AGENT_EXEC_SUB_STATE"
fi
exit 0
"""


def _playbook(tmp_path: Path, task: dict, check: bool) -> tuple[int, str, str]:
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Exercise the agent_exec task under test",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {"agent_exec_service": "agent-exec"},
                    "tasks": [task],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir(exist_ok=True)
    systemctl = stub_bin / "systemctl"
    systemctl.write_text(SYSTEMCTL_STUB, encoding="utf-8")
    systemctl.chmod(0o755)
    systemctl_log = tmp_path / "systemctl.log"
    ansible_config = tmp_path / "ansible.cfg"
    ansible_config.write_text(
        "[defaults]\n"
        "inventory = localhost,\n"
        "retry_files_enabled = False\n"
        "interpreter_python = auto_silent\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["ANSIBLE_CONFIG"] = str(ansible_config)
    env["ANSIBLE_LOCAL_TEMP"] = str(tmp_path / "ansible-local")
    env["AGENT_EXEC_SYSTEMCTL_LOG"] = str(systemctl_log)
    env["AGENT_EXEC_ACTIVE_STATE"] = "inactive" if check else "active"
    env["AGENT_EXEC_SUB_STATE"] = "dead" if check else "running"
    env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"
    argv = ["ansible-playbook", str(playbook), "-c", "local"]
    if check:
        argv.append("--check")
    result = subprocess.run(
        argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout + result.stderr, systemctl_log


def test_verify_task_skips_in_check_mode_instead_of_failing(tmp_path: Path) -> None:
    task = _task(_yaml(ROLE_MAIN), "Verify the agent-exec service is active")
    assert "not ansible_check_mode" in task["when"]

    returncode, output, _ = _playbook(tmp_path, task, check=True)

    assert returncode == 0, output
    assert "skipping" in output


def test_verify_task_still_polls_systemd_in_a_real_run(tmp_path: Path) -> None:
    task = _task(_yaml(ROLE_MAIN), "Verify the agent-exec service is active")

    returncode, output, systemctl_log = _playbook(tmp_path, task, check=False)

    assert returncode == 0, output
    assert "show agent-exec" in systemctl_log.read_text(encoding="utf-8")


def test_only_the_venv_and_verify_tasks_gate_on_check_mode() -> None:
    tasks = _yaml(ROLE_MAIN)
    pip_task = _task(tasks, "Create the venv and install the agent runtime + OpenLLMetry packages")
    verify_task = _task(tasks, "Verify the agent-exec service is active")

    assert "not ansible_check_mode" in pip_task.get("when", ""), pip_task["name"]
    assert "not ansible_check_mode" in verify_task.get("when", ""), verify_task["name"]

    # The platform tasks must keep previewing in check mode; a role-wide gate
    # would turn --check into a no-op for everything else.
    gated = (pip_task["name"], verify_task["name"])
    for task in tasks:
        assert "not ansible_check_mode" not in task.get("when", "") or task["name"] in gated, (
            task["name"]
        )
