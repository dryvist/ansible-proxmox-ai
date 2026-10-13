"""The shared guest role installs only version-pinned NVIDIA userspace."""

from __future__ import annotations

import base64
import re
import subprocess
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
    environment = jinja2.Environment()
    environment.filters["regex_replace"] = lambda value, pattern, repl: re.sub(pattern, repl, value)
    rendered = environment.from_string(installs[1]["name"]).render(
        nvidia_gpu_guest_nvidia_userspace_version="595.91.07",
        nvidia_gpu_guest_stale_nvidia_packages={"stdout_lines": ["nvidia-driver", "libcuda1"]},
    )
    assert yaml.safe_load(rendered) == [
        "nvidia-vulkan-icd=595.91.07-*",
        "libcuda1=595.91.07-*",
        "nvidia-driver-cuda=595.91.07-*",
        "nvidia-driver=595.91.07-*",
    ]
    assert installs[1]["install_recommends"] is False
    assert installs[1]["allow_downgrade"] is True


def test_nvidia_package_installs_skip_only_for_a_new_repo_in_check_mode():
    guard = (
        "not (ansible_check_mode and "
        "(nvidia_gpu_guest_nvidia_repo | default({})).changed | default(false))"
    )
    tasks = _load(USERSPACE_TASKS)
    package_installs = [
        task for task in tasks if "ansible.builtin.apt" in task
    ] + _load(ROLE_ROOT / "tasks/install-cuda-toolkit.yml")
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    template = environment.from_string("{{ " + guard + " }}")
    scenarios = [
        {"ansible_check_mode": True, "nvidia_gpu_guest_nvidia_repo": {"changed": True}},
        {"ansible_check_mode": True, "nvidia_gpu_guest_nvidia_repo": {"changed": False}},
        {"ansible_check_mode": False, "nvidia_gpu_guest_nvidia_repo": {"changed": True}},
    ]

    assert (
        len(package_installs) == 3
        and [task.get("when") for task in package_installs] == [[guard]] * 3
        and [yaml.safe_load(template.render(**scenario)) for scenario in scenarios]
        == [False, True, True]
    )


def test_stale_package_scan_selects_every_installed_package_at_another_version(tmp_path):
    # Shape of a guest left with a newer userspace than the host module.
    fixture = tmp_path / "dpkg.txt"
    fixture.write_text(
        "ii nvidia-driver 610.57.04-1\n"
        "ii nvidia-kernel-support 610.57.04-1\n"
        "ii libnvidia-ml1 610.57.04-1\n"
        "ii libcuda1 595.91.07-1\n"
        "rc nvidia-powerd 610.57.04-1\n"
        "ii nvidia-container-toolkit 1.17.8-1\n"
        "ii libvulkan1 1.4.309.0-1\n"
        "ii curl 8.14.1-2\n",
        encoding="utf-8",
    )
    scan = next(
        task for task in _load(USERSPACE_TASKS)
        if task.get("name") == "List installed NVIDIA packages at another driver version"
    )
    command = jinja2.Environment().from_string(scan["ansible.builtin.shell"]).render(
        nvidia_gpu_guest_nvidia_userspace_version="595.91.07"
    )
    command = re.sub(r"dpkg-query -W -f='[^']*'", f"cat {fixture}", command)
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, check=True)

    assert result.stdout.split() == ["nvidia-driver", "nvidia-kernel-support", "libnvidia-ml1"]


def test_shared_gpu_role_keeps_userspace_and_cuda_in_common_setup():
    tasks = _load(ROLE_ROOT / "tasks/main.yml")
    names = [task["name"] for task in tasks]

    assert any("NVIDIA userspace" in name for name in names)
    assert any("CUDA toolkit" in name for name in names)
    assert all("vllm" not in name.lower() and "llama.cpp" not in name.lower() for name in names)
