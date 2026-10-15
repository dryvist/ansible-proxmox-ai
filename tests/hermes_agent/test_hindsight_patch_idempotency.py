"""Run the Hindsight source patches twice with Ansible's real replace module."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

from _pinned_sources_worker import PINNED_HINDSIGHT_FAILURE_SOURCE

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles" / "hermes_agent"
FAILURE_TASK = "Return the queued Hindsight failure marker with prefetch context"
TIMEOUT_TASK = "Patch Hindsight prefetch join timeout off the hardcoded 3.0s"
TASK_ORDERS = (
    ("failure-first", (FAILURE_TASK, TIMEOUT_TASK)),
    ("timeout-first", (TIMEOUT_TASK, FAILURE_TASK)),
)
TASK_FILES = {
    FAILURE_TASK: "patches_hindsight_failures.yml",
    TIMEOUT_TASK: "patches_hindsight_prefetch.yml",
}


def _task(filename: str, name: str) -> dict:
    tasks = yaml.safe_load((ROLE / "tasks" / filename).read_text())
    return next(task for task in tasks if task["name"] == name)


def _run(tmp_path: Path, tasks: list[dict], variables: dict) -> tuple[int, str]:
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
                    "name": "Apply Hindsight source patches",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": variables,
                    "tasks": tasks,
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
    return int(match.group(1)), output


def test_hindsight_patches_converge_in_either_order_and_are_idempotent(tmp_path):
    final_contents = []

    for label, names in TASK_ORDERS:
        root = tmp_path / label
        install_dir = root / "hermes"
        source = install_dir / "plugins" / "memory" / "hindsight" / "__init__.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(PINNED_HINDSIGHT_FAILURE_SOURCE.encode())
        tasks = [_task(TASK_FILES[name], name) for name in names]
        variables = {
            "hermes_agent_install_dir": str(install_dir),
            "hermes_agent_memory_provider": "hindsight",
            "hermes_agent_hindsight_prefetch_timeout_seconds": 10,
        }

        first_changed, _ = _run(root, tasks, variables)
        first_contents = source.read_bytes()
        assert first_changed > 0

        second_changed, second_output = _run(root, tasks, variables)
        second_contents = source.read_bytes()
        assert second_contents == first_contents
        assert b"self._join_prefetch(10.0, log=True)" in second_contents
        assert second_changed == 0, second_output
        final_contents.append(second_contents)

    assert final_contents[0] == final_contents[1]


def test_timeout_patch_covers_a_tree_the_full_prefetch_replacement_misses(tmp_path):
    install_dir = tmp_path / "hermes"
    source = install_dir / "plugins" / "memory" / "hindsight" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class HindsightMemoryProvider:\n"
        "    def prefetch(self, query: str, *, session_id: str = \"\") -> str:\n"
        "        self._join_prefetch(3.0, log=True)\n"
    )
    tasks = [
        _task("patches_hindsight_failures.yml", FAILURE_TASK),
        _task("patches_hindsight_prefetch.yml", TIMEOUT_TASK),
    ]
    variables = {
        "hermes_agent_install_dir": str(install_dir),
        "hermes_agent_memory_provider": "hindsight",
        "hermes_agent_hindsight_prefetch_timeout_seconds": 10,
    }

    first_changed, _ = _run(tmp_path, tasks, variables)
    first_contents = source.read_bytes()
    second_changed, second_output = _run(tmp_path, tasks, variables)

    assert first_changed == 1
    assert b"self._join_prefetch(10.0, log=True)" in first_contents
    assert source.read_bytes() == first_contents
    assert second_changed == 0, second_output
