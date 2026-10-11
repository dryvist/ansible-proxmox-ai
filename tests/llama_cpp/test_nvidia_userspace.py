"""Contract for NVIDIA userspace installation in the llama.cpp guest."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/llama_cpp"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_nvidia_userspace_runs_before_the_vulkan_visibility_gate():
    main_tasks = _load(ROLE_ROOT / "tasks/main.yml")
    access = next(
        task
        for task in main_tasks
        if task.get("ansible.builtin.include_tasks") == "gpu_access.yml"
    )
    assert access["when"] == "llama_cpp_gpu | bool"

    tasks = _load(ROLE_ROOT / "tasks/gpu_access.yml")
    installer_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name") == "Install NVIDIA userspace from the host module version"
    )
    vulkan_gate_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name") == "Probe Vulkan device visibility (vulkan backend)"
    )

    assert installer_index < vulkan_gate_index


def test_nvidia_userspace_runs_only_for_nvidia_backed_vulkan_guests():
    tasks = _load(ROLE_ROOT / "tasks/gpu_access.yml")
    installer = next(
        task
        for task in tasks
        if task.get("name") == "Install NVIDIA userspace from the host module version"
    )
    include = installer["ansible.builtin.include_role"]
    conditions = installer["when"]
    expression = " and ".join(f"({condition})" for condition in conditions)
    template = jinja2.Environment().from_string("{{ " + expression + " }}")

    assert include == {
        "name": "nvidia_gpu_guest",
        "tasks_from": "install-nvidia-userspace.yml",
    }

    def should_install(backend: str, device: bool, procfs: bool) -> bool:
        return template.render(
            llama_cpp_gpu_backend=backend,
            llama_cpp_nvidia_exposure={
                "results": [
                    {"stat": {"exists": device}},
                    {"stat": {"exists": procfs}},
                ]
            },
        ) == "True"

    assert should_install("vulkan", device=True, procfs=False)
    assert should_install("vulkan", device=False, procfs=True)
    assert not should_install("vulkan", device=False, procfs=False)
    assert not should_install("rocm", device=False, procfs=True)
    assert not should_install("cpu", device=False, procfs=True)
