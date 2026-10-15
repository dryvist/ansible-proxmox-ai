"""Run the cron classifier source patch twice with Ansible's real replace module."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

from test_cron_wall_clock_classification import UPSTREAM_EXCERPT

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles" / "hermes_agent"
TASK_NAME = "Classify local stops and fallback exhaustion in delivered failures"
CLASSIFIER = 'if "exceeded aggregate wall clock" in lower:'


def test_classifier_patch_is_idempotent_with_the_real_replace_module(tmp_path):
    install_dir = tmp_path / "hermes"
    source = install_dir / "cron" / "scheduler.py"
    source.parent.mkdir(parents=True)
    source.write_text(UPSTREAM_EXCERPT)
    tasks = yaml.safe_load(
        (ROLE / "tasks" / "patches_cron_wall_clock.yml").read_text()
    )
    task = next(item for item in tasks if item["name"] == TASK_NAME)
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
                    "name": "Apply cron classifier patch",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {"hermes_agent_install_dir": str(install_dir)},
                    "tasks": [task],
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

    def run() -> tuple[int, str]:
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
        return int(match.group(1)), output

    first_changed, _ = run()
    first_contents = source.read_bytes()
    second_changed, second_output = run()
    second_contents = source.read_bytes()

    assert first_changed > 0
    assert second_contents.count(CLASSIFIER.encode()) == 1
    assert second_contents == first_contents
    assert second_changed == 0, second_output
