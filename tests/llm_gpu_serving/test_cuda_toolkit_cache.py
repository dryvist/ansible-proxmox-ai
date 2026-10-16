"""Check the CUDA install refreshes apt only after an NVIDIA repo change."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = REPO_ROOT / "roles/llm_gpu_serving/tasks/install-cuda-toolkit.yml"


def test_gpu_guest_roles_pin_the_latest_cuda_minor():
    for role in ("nvidia_gpu_guest", "llm_gpu_serving"):
        defaults = yaml.safe_load(
            (REPO_ROOT / f"roles/{role}/defaults/main/00-core.yml").read_text(
                encoding="utf-8"
            )
        )
        assert defaults[f"{role}_cuda_version"] == "13.4"


def test_cuda_cache_refresh_tracks_the_shared_repo_result_and_skips_safely():
    task = yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))[0]
    expression = task["ansible.builtin.apt"]["update_cache"]
    template = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(expression)
    guard = (
        "not (ansible_check_mode and "
        "(nvidia_gpu_guest_nvidia_repo | default({})).changed | default(false))"
    )
    guard_template = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(
        "{{ " + guard + " }}"
    )
    scenarios = [
        {"ansible_check_mode": True, "nvidia_gpu_guest_nvidia_repo": {"changed": True}},
        {"ansible_check_mode": True, "nvidia_gpu_guest_nvidia_repo": {"changed": False}},
        {"ansible_check_mode": False, "nvidia_gpu_guest_nvidia_repo": {"changed": True}},
    ]

    assert yaml.safe_load(template.render(nvidia_gpu_guest_nvidia_repo={"changed": True})) is True
    assert yaml.safe_load(template.render(nvidia_gpu_guest_nvidia_repo={"changed": False})) is False
    assert yaml.safe_load(template.render(nvidia_gpu_guest_nvidia_repo={"skipped": True})) is False
    assert yaml.safe_load(template.render()) is False
    assert task.get("when") == [guard] and [
        yaml.safe_load(guard_template.render(**scenario)) for scenario in scenarios
    ] == [False, True, True]
