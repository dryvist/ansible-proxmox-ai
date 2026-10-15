"""Keep config changes on the gateway input-digest restart path."""
from __future__ import annotations

import copy
import hashlib
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ROLE = REPO / "roles" / "hermes_agent"
RESTART = "Restart a gateway whose inputs differ from the recorded digest"


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())


def _digest(paths: list[Path]) -> str:
    inputs = [
        f"{path}={hashlib.sha256(path.read_bytes()).hexdigest()}" for path in paths
    ]
    return hashlib.sha256("\n".join(inputs).encode()).hexdigest()


def test_config_deploy_leaves_restart_to_input_digest():
    config_tasks = _tasks(ROLE / "tasks" / "config_and_content.yml")
    config = next(task for task in config_tasks if task["name"].startswith("Deploy the Hermes config.yaml"))
    assert "notify" not in config

    final_tasks = _tasks(ROLE / "tasks" / "finalize.yml")
    stat = next(task for task in final_tasks if task["name"] == "Stat the gateway input files")
    assert "{{ hermes_agent_hermes_home }}/config.yaml" in stat["loop"]


def test_real_ansible_restart_decision_tracks_config_digest(tmp_path: Path):
    env_file = tmp_path / ".env"
    config_file = tmp_path / "config.yaml"
    unit_file = tmp_path / "hermes-gateway.service"
    marker = tmp_path / "gateway-restart.marker"
    paths = [env_file, config_file, unit_file]
    for path in paths:
        path.write_text("old input\n")
    old_digest = _digest(paths)
    marker.write_text(old_digest)
    config_file.write_text("changed config\n")

    input_tasks = _tasks(ROLE / "tasks" / "finalize.yml")
    names = {
        "Stat the gateway input files",
        "Set the desired gateway input digest",
        "Stat the gateway restart marker",
        "Read the recorded gateway digest",
        RESTART,
        "Flush handlers so config/unit changes apply before verification",
    }
    tasks = [copy.deepcopy(task) for task in input_tasks if task["name"] in names]
    next(task for task in tasks if task["name"] == "Stat the gateway input files")["loop"] = [
        str(path) for path in paths
    ]

    record = next(
        handler
        for handler in _tasks(ROLE / "handlers" / "main.yml")
        if handler["name"] == "Record the gateway restart"
    )
    record_copy = record["ansible.builtin.copy"]
    record_copy.pop("owner", None)
    record_copy.pop("group", None)
    handlers = [
        {
            "name": "Observe gateway restart",
            "listen": "Restart hermes-gateway",
            "ansible.builtin.debug": {"msg": "GATEWAY_RESTART_HANDLER_FIRED"},
        },
        record,
    ]
    playbook = tmp_path / "gateway-digest.yml"
    inventory = tmp_path / "inventory.yml"
    inventory.write_text(
        yaml.safe_dump({"all": {"hosts": {"localhost": {"ansible_connection": "local"}}}})
    )
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Validate gateway restart digest with Ansible",
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars": {
                        "hermes_agent_hermes_home": str(tmp_path),
                        "hermes_agent_gateway_restart_marker": str(marker),
                    },
                    "tasks": tasks,
                    "handlers": handlers,
                }
            ],
            sort_keys=False,
        )
    )

    changed = subprocess.run(
        ["ansible-playbook", "--inventory", str(inventory), str(playbook)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert "RUNNING HANDLER [Observe gateway restart]" in changed.stdout

    unchanged = subprocess.run(
        ["ansible-playbook", "--inventory", str(inventory), str(playbook)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert unchanged.returncode == 0, unchanged.stdout + unchanged.stderr
    assert "RUNNING HANDLER [Observe gateway restart]" not in unchanged.stdout
