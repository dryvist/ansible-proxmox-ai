"""Time budgets and Ansible async failure handling for held-out evaluation."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles/laya_systemone"
TASKS = ROLE / "tasks"
PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-gpu-engines.yml"


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _timeout_seconds(task: dict, defaults: dict) -> int:
    value = task["timeout"]
    if isinstance(value, int):
        return value
    variable = value.strip().removeprefix("{{").removesuffix("}}").strip()
    return int(defaults[variable])


def _walk(tasks: list[dict]):
    for task in tasks:
        yield task
        for section in ("block", "rescue", "always"):
            nested = task.get(section)
            if isinstance(nested, list):
                yield from _walk(nested)


def test_health_wait_and_heldout_job_rail_leave_required_runway() -> None:
    core = _load_yaml(ROLE / "defaults/main/00-core.yml")
    heldout = _load_yaml(ROLE / "defaults/main/30-heldout.yml")
    health_budget = int(core["laya_systemone_health_budget_seconds"])
    health_timeout = int(core["laya_systemone_health_timeout_seconds"])
    health_delay = int(core["laya_systemone_health_delay"])
    health_retries = health_budget // (health_timeout + health_delay) - 1
    serving_worst_case = (health_retries + 1) * health_timeout + health_retries * health_delay
    assert "laya_systemone_health_budget_seconds" in core["laya_systemone_health_retries"]
    assert "laya_systemone_health_timeout_seconds" in core["laya_systemone_health_retries"]
    assert "laya_systemone_health_delay" in core["laya_systemone_health_retries"]
    assert "//" in core["laya_systemone_health_retries"]
    assert "- 1" in core["laya_systemone_health_retries"]
    assert health_budget == 180
    assert health_timeout == 15
    assert health_delay == 5
    assert core["laya_systemone_health_task_timeout_seconds"] >= 30
    assert health_retries == 8
    assert serving_worst_case <= health_budget
    assert serving_worst_case == 175

    total = int(heldout["laya_systemone_heldout_total_budget_seconds"])
    async_timeout = int(heldout["laya_systemone_heldout_run_timeout_seconds"])
    poll_seconds = int(heldout["laya_systemone_heldout_run_poll_seconds"])
    run_budget = int(heldout["laya_systemone_heldout_run_budget_seconds"])
    run = _task(_load_yaml(TASKS / "heldout-eval.yml"), "Run the held-out evaluation on the guest")
    evaluation = _task(run["block"], "Run the held-out evaluation command")
    budget_guard = _task(_load_yaml(TASKS / "heldout-eval.yml"), "Validate the held-out component time budget")
    guards = budget_guard["ansible.builtin.assert"]["that"]
    assert total == 540
    assert total < 600
    assert run_budget == async_timeout + poll_seconds == 365
    assert evaluation["async"] == "{{ laya_systemone_heldout_run_timeout_seconds }}"
    assert evaluation["poll"] == "{{ laya_systemone_heldout_run_poll_seconds }}"
    assert evaluation["timeout"] == "{{ laya_systemone_heldout_run_budget_seconds }}"
    assert total - run_budget >= 120
    assert any("laya_systemone_heldout_total_budget_seconds" in guard and "< 600" in guard for guard in guards)
    assert any("laya_systemone_heldout_total_budget_seconds" in guard and ">= 120" in guard for guard in guards)


def test_heldout_path_timeouts_meet_measured_task_floors() -> None:
    core = _load_yaml(ROLE / "defaults/main/00-core.yml")
    heldout_defaults = _load_yaml(ROLE / "defaults/main/30-heldout.yml")
    defaults = {**core, **heldout_defaults}
    credential_defaults = {
        **defaults,
        "laya_systemone_rustfs_awscli_install_timeout_seconds": heldout_defaults[
            "laya_systemone_heldout_awscli_install_timeout_seconds"
        ],
    }
    tasks = _load_yaml(TASKS / "heldout-eval.yml")
    health_tasks = _load_yaml(TASKS / "health.yml")
    credential_tasks = _load_yaml(TASKS / "rustfs-credential.yml")
    heldout_play = next(
        play for play in _load_yaml(PLAYBOOK) if play.get("name") == "Score Laya models on held-out data"
    )
    render_tasks = _load_yaml(TASKS / "render-unit.yml")
    timeout_tasks = [
        *[(task, defaults) for task in _walk(tasks)],
        *[(task, defaults) for task in _walk(health_tasks)],
        *[(task, credential_defaults) for task in _walk(credential_tasks)],
        *[(task, defaults) for task in _walk(heldout_play["pre_tasks"] + heldout_play["tasks"])],
        *[(task, defaults) for task in _walk(render_tasks)],
    ]
    measured_floors = {
        "Create private held-out evaluation directories": 67,
        "Install the held-out evaluation script": 51,
        "Download the held-out CSV when missing or corrupt": 60,
        "Install the AWS CLI used for RustFS object reads": 60,
        "Write the Laya bearer key environment file": 35,
        "Wait for Laya health to return successfully": 30,
        "Copy the held-out evaluation result to the bucket": 60,
    }

    for task, task_defaults in timeout_tasks:
        if "timeout" not in task:
            continue
        floor = measured_floors.get(task.get("name", ""), 30)
        assert _timeout_seconds(task, task_defaults) >= floor, task.get("name")

    budget_guard = _task(tasks, "Validate the held-out component time budget")["ansible.builtin.assert"]["that"]
    timeout_floors = {
        "laya_systemone_heldout_module_timeout_seconds": 67,
        "laya_systemone_heldout_command_timeout_seconds": 30,
        "laya_systemone_heldout_network_timeout_seconds": 60,
        "laya_systemone_heldout_rustfs_request_timeout_seconds": 30,
        "laya_systemone_heldout_awscli_install_timeout_seconds": 60,
        "laya_systemone_heldout_health_timeout_seconds": 30,
        "laya_systemone_health_task_timeout_seconds": 30,
    }
    for variable, floor in timeout_floors.items():
        assert int(defaults[variable]) >= floor
        assert any(variable in guard and f">= {floor}" in guard for guard in budget_guard)

    credential = _task(tasks, "Read the scoped RustFS credential for the held-out CSV")
    assert credential["vars"]["laya_systemone_rustfs_request_timeout_seconds"] == (
        "{{ laya_systemone_heldout_rustfs_request_timeout_seconds }}"
    )
    assert "laya_systemone_rustfs_awscli_installed" in _task(
        credential_tasks, "Install the AWS CLI used for RustFS object reads"
    )["when"]


def test_ansible_async_expiry_fails_through_heldout_rescue(tmp_path: Path) -> None:
    ansible_playbook = shutil.which("ansible-playbook")
    if ansible_playbook is None:
        pytest.skip("ansible-playbook is not on PATH")

    error_path = tmp_path / "heldout.error"
    playbook_path = tmp_path / "async-expiry.yml"
    fallback_message = "Held-out evaluation failed without a sanitized error sidecar."
    playbook = [
        {
            "name": "Exercise held-out async expiry",
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": [
                {
                    "name": "Run the held-out evaluation on the guest",
                    "block": [
                        {
                            "name": "Run the held-out evaluation command",
                            "ansible.builtin.command": {"cmd": "sleep 5"},
                            "async": 2,
                            "poll": 1,
                        }
                    ],
                    "rescue": [
                        {
                            "name": "Check for the sanitized held-out evaluation error sidecar",
                            "ansible.builtin.stat": {"path": str(error_path)},
                            "register": "heldout_error_stat",
                        },
                        {
                            "name": "Read the sanitized held-out evaluation error",
                            "ansible.builtin.slurp": {"src": str(error_path)},
                            "register": "heldout_error",
                            "when": "heldout_error_stat.stat.exists",
                        },
                        {
                            "name": "Fail with the sanitized held-out evaluation error",
                            "ansible.builtin.fail": {
                                "msg": "{{ heldout_error.content | default('') | b64decode | trim or '"
                                + fallback_message
                                + "' }}"
                            },
                        },
                    ],
                }
            ],
        }
    ]
    playbook_path.write_text(yaml.safe_dump(playbook, sort_keys=False), encoding="utf-8")

    completed = subprocess.run(
        [ansible_playbook, "-c", "local", "-i", "localhost,", str(playbook_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode != 0
    assert fallback_message in completed.stdout + completed.stderr
