"""The shared guest role installs only version-pinned NVIDIA userspace."""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
USERSPACE_TASKS = ROLE_ROOT / "tasks/install-nvidia-userspace.yml"
CORE_DEFAULTS = ROLE_ROOT / "defaults/main/00-core.yml"


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


def test_userspace_version_is_one_renovate_pinned_variable():
    defaults = _load(CORE_DEFAULTS)
    version = defaults["nvidia_gpu_guest_nvidia_userspace_version"]
    text = CORE_DEFAULTS.read_text(encoding="utf-8")
    pin_line = text.splitlines().index(f'nvidia_gpu_guest_nvidia_userspace_version: "{version}"')

    assert re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", version)
    assert text.splitlines()[pin_line - 1].startswith("# renovate:")
    assert version not in USERSPACE_TASKS.read_text(encoding="utf-8")


def test_userspace_install_is_userspace_only_and_pinned():
    tasks = _load(USERSPACE_TASKS)
    installs = [task for task in _walk(tasks) if "ansible.builtin.apt" in task]
    packages = yaml.safe_dump([task["ansible.builtin.apt"]["name"] for task in installs])

    assert re.search(r"dkms|kernel|nvidia-open", packages) is None
    assert installs[-1]["ansible.builtin.apt"]["install_recommends"] is False
    names = installs[-1]["ansible.builtin.apt"]["name"]
    assert all("{{ nvidia_gpu_guest_nvidia_userspace_version }}" in name for name in names)
    assert [name.split("=")[0] for name in names] == ["libcuda1", "nvidia-driver-cuda"]
    assert "nvidia-driver-pinning-" in installs[0]["ansible.builtin.apt"]["name"]


def test_userspace_install_is_skipped_when_nvidia_smi_reports_the_version():
    block = next(task for task in _load(USERSPACE_TASKS) if "block" in task)
    condition = block["when"].replace("\n", " ")
    template = jinja2.Environment().from_string("{{ " + condition + " }}")
    version = "595.91.07"

    def decide(rc: int, lines: list[str]) -> str:
        return template.render(
            nvidia_gpu_guest_nvidia_smi={"rc": rc, "stdout_lines": lines},
            nvidia_gpu_guest_nvidia_userspace_version=version,
        )

    assert decide(0, [version]) == "False"
    assert decide(0, [version, version]) == "False"
    assert decide(0, ["595.45.04"]) == "True"
    assert decide(9, []) == "True"
    assert decide(0, []) == "True"
    assert decide(127, []) == "True"


def test_shared_gpu_role_keeps_userspace_and_cuda_in_common_setup():
    tasks = _load(ROLE_ROOT / "tasks/main.yml")
    names = [task["name"] for task in tasks]

    assert any("NVIDIA userspace" in name for name in names)
    assert any("CUDA toolkit" in name for name in names)
    assert all("vllm" not in name.lower() and "llama.cpp" not in name.lower() for name in names)
