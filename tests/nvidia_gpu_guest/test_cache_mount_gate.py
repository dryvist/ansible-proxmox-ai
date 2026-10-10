"""The writable model cache mount is checked before any task changes a path under it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
NVIDIA_TASKS = REPO_ROOT / "roles/nvidia_gpu_guest/tasks"
GATE = NVIDIA_TASKS / "assert-cache-mount.yml"
ENGINE_ROLES = ("llamacpp_serving", "vllm_serving")
CHANGE_TASK = "TASK [Change a path under the mount]"
PROBE = {"name": "Change a path under the mount", "ansible.builtin.debug": {"msg": "reached"}}


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("entry", [NVIDIA_TASKS / "main.yml", NVIDIA_TASKS / "cache-sync.yml"])
def test_gate_is_the_first_task_of_each_entry_point(entry: Path) -> None:
    assert _tasks(entry)[0]["ansible.builtin.include_tasks"] == "assert-cache-mount.yml"


@pytest.mark.parametrize("role", ENGINE_ROLES)
def test_engine_roles_run_the_shared_gate_before_their_own_checks(role: str) -> None:
    main = _tasks(REPO_ROOT / f"roles/{role}/tasks/main.yml")
    assert main[0]["ansible.builtin.include_role"]["name"] == "nvidia_gpu_guest"
    validation = _tasks(REPO_ROOT / f"roles/{role}/tasks/validate-profiles.yml")
    assert all("ansible.builtin.stat" not in task for task in validation)


def _run_gate(tmp_path: Path, mount: str) -> subprocess.CompletedProcess:
    playbook = tmp_path / "gate.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Run the model-cache mount gate",
                    "hosts": "localhost",
                    "gather_facts": False,
                    "tasks": [{"ansible.builtin.include_tasks": str(GATE)}, PROBE],
                }
            ],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            "-c",
            "local",
            "-e",
            json.dumps({"nvidia_gpu_guest_model_cache_mount_path": mount}),
            str(playbook),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_empty_mount_path_fails_before_any_change(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, "")
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "writable local cache mount path must be declared" in output, output
    assert CHANGE_TASK not in output, output


def test_missing_mount_fails_before_any_change(tmp_path: Path) -> None:
    missing = tmp_path / "not-mounted"
    result = _run_gate(tmp_path, str(missing))
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert "must already exist as a directory" in output, output
    assert CHANGE_TASK not in output, output
    assert not missing.exists()


def test_existing_mount_directory_passes_and_reaches_the_change(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    result = _run_gate(tmp_path, str(cache))
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert CHANGE_TASK in output, output
