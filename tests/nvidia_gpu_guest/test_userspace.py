"""The shared guest role installs only version-pinned NVIDIA userspace."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
USERSPACE_TASKS = ROLE_ROOT / "tasks/install-nvidia-userspace.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_userspace_version_is_parsed_from_the_host_module_procfs_fixture():
    tasks = _load(USERSPACE_TASKS)
    slurp = next(
        task["ansible.builtin.slurp"]
        for task in tasks
        if "ansible.builtin.slurp" in task
    )
    parse = next(
        task["ansible.builtin.set_fact"]
        for task in tasks
        if "ansible.builtin.set_fact" in task
    )
    expression = parse["nvidia_gpu_guest_nvidia_userspace_version"]
    version = ".".join(map(str, (12, 34, 56)))
    proc_text = f"NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  {version} Release Build"
    environment = jinja2.Environment()
    environment.filters["b64decode"] = lambda content: base64.b64decode(content).decode()
    environment.filters["regex_search"] = lambda value, pattern: (
        match.group(0) if (match := re.search(pattern, value)) else None
    )

    assert slurp["src"] == "/proc/driver/nvidia/version"
    def parse_proc(text: str) -> str:
        return environment.from_string(expression).render(
            nvidia_gpu_guest_host_module_version={
                "content": base64.b64encode(text.encode()).decode()
            }
        )

    assert parse_proc(proc_text) == version
    assert parse_proc("No NVIDIA module version here") == ""


def test_userspace_version_read_and_parse_fail_with_clear_messages():
    tasks = _load(USERSPACE_TASKS)
    readable = next(
        task["ansible.builtin.assert"]
        for task in tasks
        if task.get("name") == "Assert the host NVIDIA module version is readable"
    )
    parseable = next(
        task["ansible.builtin.assert"]
        for task in tasks
        if task.get("name") == "Assert the host NVIDIA module version is parseable"
    )
    environment = jinja2.Environment()

    assert "/proc/driver/nvidia/version" in readable["fail_msg"]
    assert "/proc/driver/nvidia/version" in parseable["fail_msg"]
    assert "N.N.N" in parseable["fail_msg"]
    readable_check = environment.from_string("{{ " + readable["that"][0] + " }}")
    parseable_check = environment.from_string("{{ " + parseable["that"][0] + " }}")
    assert readable_check.render(nvidia_gpu_guest_host_module_version={}) == "False"
    assert parseable_check.render(nvidia_gpu_guest_nvidia_userspace_version="") == "False"


def test_userspace_install_is_userspace_only_and_pinned():
    tasks = _load(USERSPACE_TASKS)
    installs = [task["ansible.builtin.apt"] for task in _walk(tasks) if "ansible.builtin.apt" in task]
    packages = yaml.safe_dump([task["name"] for task in installs])

    assert re.search(r"dkms|kernel|nvidia-open", packages) is None
    assert len(installs) == 2
    assert installs[0]["name"] == (
        "nvidia-driver-pinning-{{ nvidia_gpu_guest_nvidia_userspace_version }}"
    )
    assert installs[1]["name"] == [
        "nvidia-vulkan-icd={{ nvidia_gpu_guest_nvidia_userspace_version }}-1",
        "libcuda1={{ nvidia_gpu_guest_nvidia_userspace_version }}-1",
        "nvidia-driver-cuda={{ nvidia_gpu_guest_nvidia_userspace_version }}-*",
    ]
    assert installs[1]["install_recommends"] is False
    assert installs[1]["allow_downgrade"] is True


def test_shared_gpu_role_keeps_userspace_and_cuda_in_common_setup():
    tasks = _load(ROLE_ROOT / "tasks/main.yml")
    names = [task["name"] for task in tasks]

    assert any("NVIDIA userspace" in name for name in names)
    assert any("CUDA toolkit" in name for name in names)
    assert all("vllm" not in name.lower() and "llama.cpp" not in name.lower() for name in names)
