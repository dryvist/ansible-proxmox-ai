"""The NVIDIA container toolkit role pins its inputs and converges idempotently."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/nvidia_container_toolkit"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _environment() -> jinja2.Environment:
    environment = jinja2.Environment()
    environment.filters["b64decode"] = lambda value: base64.b64decode(value).decode()
    environment.filters["from_json"] = json.loads
    environment.filters["bool"] = bool
    return environment


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def _module(task: dict, module: str):
    return task[module]


def _main_tasks() -> list[dict]:
    return _load(ROLE / "tasks/main.yml")


def test_package_and_repository_are_pinned_to_nvidia_apt():
    defaults = _load(ROLE / "defaults/main.yml")
    assert re.fullmatch(r"1\.\d+\.\d+-\d+", defaults["nvidia_container_toolkit_package_version"])
    assert defaults["nvidia_container_toolkit_repo_uri"].startswith(
        "https://nvidia.github.io/libnvidia-container/stable/deb/"
    )
    assert defaults["nvidia_container_toolkit_repo_key_url"] == "https://nvidia.github.io/libnvidia-container/gpgkey"


def test_install_task_uses_the_pinned_version_variable():
    install = _module(_task(_main_tasks(), "Install the pinned NVIDIA container toolkit"), "ansible.builtin.apt")
    pin = "={{ nvidia_container_toolkit_package_version }}"
    assert install["name"] == [
        f"{name}{pin}"
        for name in ("nvidia-container-toolkit", "nvidia-container-toolkit-base",
                     "libnvidia-container-tools", "libnvidia-container1")
    ]


def test_sample_image_is_the_documented_image_pinned_by_digest():
    defaults = _load(ROLE / "defaults/main.yml")
    assert re.fullmatch(r"ubuntu:24\.04@sha256:[0-9a-f]{64}", defaults["nvidia_container_toolkit_sample_image"])


def test_runtime_is_registered_only_when_docker_lacks_it():
    tasks = _main_tasks()
    present = _module(_task(tasks, "Check whether Docker already registers the NVIDIA runtime"), "ansible.builtin.set_fact")
    expression = present["nvidia_container_toolkit_docker_runtime_present"]
    register = _task(tasks, "Register the NVIDIA runtime with Docker")
    assert register["notify"] == "Restart Docker"

    def runtime_present(daemon_json: dict) -> bool:
        return _environment().from_string(expression).render(
            nvidia_container_toolkit_daemon_json={"content": base64.b64encode(json.dumps(daemon_json).encode()).decode()}
        ).strip() == "True"

    def register_runs(present_flag: bool) -> bool:
        return _environment().compile_expression(register["when"])(
            nvidia_container_toolkit_docker_runtime_present=present_flag
        )

    assert register_runs(runtime_present({"log-driver": "json-file"}))
    assert not register_runs(runtime_present({"runtimes": {"nvidia": {"path": "nvidia-container-runtime"}}}))
    assert register_runs(
        _environment().from_string(expression).render(nvidia_container_toolkit_daemon_json={}).strip() == "True"
    )


def test_no_cgroups_line_replaces_the_packaged_default_in_the_nvidia_section():
    tasks = _main_tasks()
    task = _module(_task(tasks, "Disable NVIDIA device-cgroup management for LXC guests"), "ansible.builtin.lineinfile")
    defaults = _load(ROLE / "defaults/main.yml")
    assert defaults["nvidia_container_toolkit_no_cgroups"] is True
    assert re.search(task["regexp"], "#no-cgroups = false", re.MULTILINE)
    assert re.search(task["regexp"], "no-cgroups = false", re.MULTILINE)
    assert task["line"] == "no-cgroups = true"
    assert re.search(task["insertafter"], "[nvidia-container-cli]", re.MULTILINE)


def test_cdi_spec_is_staged_then_copied_and_the_stage_is_removed():
    names = [task.get("name") for task in _main_tasks()]
    stage = names.index("Create a staging file for the CDI specification")
    generate = names.index("Generate the CDI specification for the host GPUs into the staging file")
    install = names.index("Install the CDI specification")
    remove = names.index("Remove the CDI staging file")
    assert stage < generate < install < remove
    generate_task = _task(_main_tasks(), "Generate the CDI specification for the host GPUs into the staging file")
    assert generate_task["changed_when"] is False
    assert "{{ nvidia_container_toolkit_cdi_stage.path }}" in generate_task["ansible.builtin.command"]["argv"][3]
    copy = _module(_task(_main_tasks(), "Install the CDI specification"), "ansible.builtin.copy")
    assert copy["remote_src"] is True
    assert copy["src"] == "{{ nvidia_container_toolkit_cdi_stage.path }}"
    assert _task(_main_tasks(), "Remove the CDI staging file")["check_mode"] is False


def test_verification_runs_after_the_docker_restart_and_not_in_check_mode():
    tasks = _main_tasks()
    names = [task.get("name") for task in tasks]
    assert names.index("Apply pending Docker restarts before verifying GPU access") < names.index(
        "Verify GPU access from a throwaway container"
    )
    assert _task(tasks, "Apply pending Docker restarts before verifying GPU access")["ansible.builtin.meta"] == (
        "flush_handlers"
    )
    verify_include = _task(tasks, "Verify GPU access from a throwaway container")
    assert verify_include["ansible.builtin.include_tasks"] == "verify.yml"
    assert verify_include["when"] == "not ansible_check_mode"


def test_sample_container_is_removed_and_lists_gpus():
    verify = _load(ROLE / "tasks/verify.yml")
    run_task = _task(verify, "Run nvidia-smi in a throwaway container from the pinned sample image")
    argv = _module(run_task, "ansible.builtin.command")["argv"]
    assert "--rm" in argv
    assert argv[argv.index("--runtime=nvidia") + 1 : argv.index("--runtime=nvidia") + 3] == ["--gpus", "all"]
    assert argv[-2:] == ["nvidia-smi", "-L"]
    assert "{{ nvidia_container_toolkit_sample_image }}" in argv
    assert run_task["changed_when"] is False

    assertion = _module(_task(verify, "Assert the sample container lists at least one GPU"), "ansible.builtin.assert")
    expression = assertion["that"][0]
    environment = _environment()
    listed = "GPU 0: NVIDIA RTX PRO 6000 Blackwell (UUID: GPU-0000)"
    assert environment.compile_expression(expression)(nvidia_container_toolkit_verify={"stdout": listed})
    assert not environment.compile_expression(expression)(
        nvidia_container_toolkit_verify={"stdout": "No devices were found"}
    )


def test_docker_restart_handler_restarts_the_docker_service():
    handler = _load(ROLE / "handlers/main.yml")[0]
    assert handler["name"] == "Restart Docker"
    assert handler["ansible.builtin.systemd"] == {"name": "docker", "state": "restarted"}
