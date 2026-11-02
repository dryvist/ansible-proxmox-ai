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


def test_health_and_heldout_budgets_fit_under_ten_minutes() -> None:
    core = _load_yaml(ROLE / "defaults/main/00-core.yml")
    heldout = _load_yaml(ROLE / "defaults/main/30-heldout.yml")
    play = next(play for play in _load_yaml(PLAYBOOK) if play.get("name") == "Score Laya models on held-out data")

    health_budget = int(core["laya_systemone_health_budget_seconds"])
    health_timeout = int(core["laya_systemone_health_timeout_seconds"])
    health_delay = int(core["laya_systemone_health_delay"])
    health_retries = health_budget // (health_timeout + health_delay) - 1
    serving_worst_case = (health_retries + 1) * health_timeout + health_retries * health_delay
    heldout_health = (
        (int(heldout["laya_systemone_heldout_health_retries"]) + 1)
        * int(heldout["laya_systemone_heldout_health_timeout_seconds"])
        + int(heldout["laya_systemone_heldout_health_retries"])
        * int(heldout["laya_systemone_heldout_health_delay_seconds"])
    )
    parts = [
        int(play["vars"]["laya_systemone_heldout_preflight_budget_seconds"]),
        int(heldout["laya_systemone_heldout_health_budget_seconds"]),
        int(heldout["laya_systemone_heldout_setup_budget_seconds"]),
        int(heldout["laya_systemone_heldout_run_budget_seconds"]),
        int(heldout["laya_systemone_heldout_upload_budget_seconds"]),
        int(heldout["laya_systemone_heldout_orchestration_budget_seconds"]),
    ]

    assert "laya_systemone_health_budget_seconds" in core["laya_systemone_health_retries"]
    assert "laya_systemone_health_timeout_seconds" in core["laya_systemone_health_retries"]
    assert "laya_systemone_health_delay" in core["laya_systemone_health_retries"]
    assert "//" in core["laya_systemone_health_retries"]
    assert "- 1" in core["laya_systemone_health_retries"]
    assert serving_worst_case <= health_budget
    assert heldout_health <= int(heldout["laya_systemone_heldout_health_budget_seconds"])
    assert int(heldout["laya_systemone_heldout_run_budget_seconds"]) == (
        int(heldout["laya_systemone_heldout_run_timeout_seconds"])
        + int(heldout["laya_systemone_heldout_run_poll_seconds"])
    )
    run = _task(_load_yaml(TASKS / "heldout-eval.yml"), "Run the held-out evaluation on the guest")
    evaluation = _task(run["block"], "Run the held-out evaluation command")
    assert evaluation["timeout"] == "{{ laya_systemone_heldout_run_budget_seconds }}"
    assert sum(parts) == int(heldout["laya_systemone_heldout_total_budget_seconds"])
    assert health_budget + sum(parts) < 600
    assert serving_worst_case + sum(parts) < 600

    budget_guard = _task(_load_yaml(TASKS / "heldout-eval.yml"), "Validate the held-out component time budget")
    assert any("laya_systemone_health_budget_seconds" in clause for clause in budget_guard["ansible.builtin.assert"]["that"])


def test_heldout_task_timeouts_fit_setup_and_upload_budgets() -> None:
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
    credential_tasks = _load_yaml(TASKS / "rustfs-credential.yml")

    setup_task_names = (
        "Create private held-out evaluation directories",
        "Check the existing held-out CSV checksum before download",
        "Download the held-out CSV when missing or corrupt",
        "Check the downloaded held-out CSV checksum",
        "Move the verified held-out CSV into place",
        "Set private ownership and permissions on the held-out CSV",
        "Read the held-out CSV checksum",
        "Create the root-owned held-out evaluation script directory",
        "Install the held-out evaluation script",
        "Read the installed Laya version for held-out evaluation",
        "Read the fine-tune manifest for held-out identity",
        "Check whether the held-out result already exists",
        "Remove a stale held-out evaluation error sidecar",
    )
    setup_seconds = 0
    for name in setup_task_names:
        task = _task(tasks, name)
        setup_seconds += _timeout_seconds(task, defaults) * (len(task.get("loop", [])) or 1)
    setup_seconds += _timeout_seconds(
        _task(credential_tasks, "Install the AWS CLI used for RustFS object reads"), credential_defaults
    )
    setup_seconds += 3 * int(heldout_defaults["laya_systemone_heldout_rustfs_request_timeout_seconds"])

    upload = _task(tasks, "Upload the held-out evaluation result")
    credential = _task(upload["block"], "Read the writable RustFS credential for held-out result upload")
    upload_command = _task(upload["block"], "Copy the held-out evaluation result to the bucket")
    upload_cleanup = _task(upload["rescue"], "Remove the local held-out result so the next converge retries the upload")
    upload_seconds = (
        3 * int(heldout_defaults["laya_systemone_heldout_rustfs_request_timeout_seconds"])
        + _timeout_seconds(upload_command, defaults)
        + _timeout_seconds(upload_cleanup, defaults)
    )

    assert credential["vars"]["laya_systemone_rustfs_request_timeout_seconds"] == (
        "{{ laya_systemone_heldout_rustfs_request_timeout_seconds }}"
    )
    assert "laya_systemone_rustfs_awscli_installed" in _task(
        credential_tasks, "Install the AWS CLI used for RustFS object reads"
    )["when"]
    assert setup_seconds <= int(heldout_defaults["laya_systemone_heldout_setup_budget_seconds"])
    assert upload_seconds <= int(heldout_defaults["laya_systemone_heldout_upload_budget_seconds"])


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
