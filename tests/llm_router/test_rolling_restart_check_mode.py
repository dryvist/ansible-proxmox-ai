"""Execute the helper's pending restart task with role defaults in check mode."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
RESTART_TASKS = REPO_ROOT / "playbooks/tasks/llm_router_rolling_restart.yml"
SYSTEMCTL_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$LLM_ROUTER_SYSTEMCTL_LOG"
if [ "$1" = "--version" ]; then
  printf 'systemd 252\\n'
elif [ "$1" = "show" ]; then
  printf 'Id=litellm.service\\nLoadState=loaded\\nActiveState=active\\nSubState=running\\n'
fi
exit 0
"""


def test_pending_restart_resolves_the_role_service_in_check_mode(tmp_path: Path) -> None:
    helper = yaml.safe_load(RESTART_TASKS.read_text(encoding="utf-8"))
    block = next(
        task
        for task in helper
        if task.get("name") == "Restart pending members and verify every member"
    )
    restart_task = next(task for task in block["block"] if task.get("name") == "Restart litellm")
    play_vars = {
        "llm_router_restart_pending": True,
        "ansible_service_mgr": "systemd",
        "ansible_system": "Linux",
    }
    assert "llm_router_service" not in play_vars
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Run the pending restart task from the rolling helper",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": play_vars,
                    "tasks": [restart_task],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    systemctl = stub_bin / "systemctl"
    systemctl.write_text(SYSTEMCTL_STUB, encoding="utf-8")
    systemctl.chmod(0o755)
    systemctl_log = tmp_path / "systemctl.log"
    ansible_config = tmp_path / "ansible.cfg"
    ansible_config.write_text(
        f"[defaults]\nroles_path = {REPO_ROOT / 'roles'}\nretry_files_enabled = False\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["ANSIBLE_CONFIG"] = str(ansible_config)
    env["ANSIBLE_LOCAL_TEMP"] = str(tmp_path / "ansible-local")
    env["LLM_ROUTER_SYSTEMCTL_LOG"] = str(systemctl_log)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["ansible-playbook", str(playbook), "-i", "localhost,", "-c", "local", "--check", "-v"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert '"name": "litellm"' in result.stdout
    assert "show litellm" in systemctl_log.read_text(encoding="utf-8")
