"""Run the cron wall-clock source patches and their real read-back assert."""

from __future__ import annotations

import re
import subprocess
from copy import deepcopy
from pathlib import Path

import yaml

from _pinned_sources import PINNED_CRON_TIMEOUT_SOURCE
from conftest import ROLE_ROOT
from test_cron_wall_clock_classification import UPSTREAM_EXCERPT

ROOT = Path(__file__).resolve().parents[2]
PATCH_TASKS = ROLE_ROOT / "tasks" / "patches_cron_wall_clock.yml"
VERIFY_TASKS = ROLE_ROOT / "tasks" / "patches_verify_cron_wall_clock.yml"
VERIFY_TASK_NAME = "Assert installed aggregate cron wall-clock patch"


def _upstream_scheduler_excerpt() -> str:
    """Join the pinned monitor excerpt with its classifier/advice excerpts."""
    return (
        "def call_watchdog():\n"
        "        agent, prompt, job, job_id, job_name, scope.task_id, cancel_event,\n"
        "\n"
        "def _run_agent_with_watchdog(\n"
        "    agent, prompt: str, job: dict, job_id: str, job_name: str, task_id: str, cancel_event,\n"
        "):\n"
        + PINNED_CRON_TIMEOUT_SOURCE
        + "\n\ndef run_job():\n    pass\n"
        + UPSTREAM_EXCERPT
        + '\n    return (\n'
        '        "No backup provider is configured — add one with `hermes fallback add`, "\n'
        '        "or set a cron-wide default via `cron.model` + `cron.model_provider` in config.yaml."\n'
        "    )\n"
        + '        f"Fix its prompt/config, or pause it with `hermes cron pause {job_ref}` "\n'
        + '        "(resume/remove also available) to stop the noise."\n'
    )


def test_cron_wall_clock_verify_assert_runs_with_real_ansible(tmp_path: Path) -> None:
    install_dir = tmp_path / "hermes"
    source = install_dir / "cron" / "scheduler.py"
    source.parent.mkdir(parents=True)
    source.write_text(_upstream_scheduler_excerpt())

    patch_tasks = yaml.safe_load(PATCH_TASKS.read_text())
    verify_tasks = yaml.safe_load(VERIFY_TASKS.read_text())
    verify = deepcopy(next(task for task in verify_tasks if task["name"] == VERIFY_TASK_NAME))
    # The included role task reads guest slurp output. This test slurps its
    # patched local fixture below, then runs the unchanged assert and `that` list.
    verify.pop("vars")
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        yaml.safe_dump(
            {"all": {"hosts": {"localhost": {"ansible_connection": "local"}}}}
        )
    )
    playbook = tmp_path / "playbook.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Apply and verify cron wall-clock source patches",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {
                        "hermes_agent_install_dir": str(install_dir),
                        "hermes_agent_cron_wall_timeout_seconds": 1800,
                    },
                    "tasks": patch_tasks
                    + [
                        {
                            "name": "Read patched scheduler excerpt",
                            "ansible.builtin.slurp": {"src": str(source)},
                            "register": "patched_scheduler_file",
                        },
                        {
                            "name": "Set verify source from patched excerpt",
                            "ansible.builtin.set_fact": {
                                "hermes_agent_cron_scheduler_source": "{{ patched_scheduler_file.content | b64decode }}"
                            },
                        },
                        verify,
                    ],
                    "handlers": [
                        {
                            "name": "Restart hermes-gateway",
                            "ansible.builtin.debug": {"msg": "restart notified"},
                        }
                    ],
                }
            ],
            sort_keys=False,
        )
    )

    result = subprocess.run(
        [
            "ansible-playbook",
            "--inventory",
            str(inventory),
            "--connection",
            "local",
            str(playbook),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    recap = result.stdout.rsplit("PLAY RECAP", 1)[-1]
    match = re.search(r"localhost\s*:\s+ok=\d+\s+changed=(\d+)", recap)
    assert match is not None, output
    assert int(match.group(1)) > 0, output
