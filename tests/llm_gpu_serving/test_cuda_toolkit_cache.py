"""Check the CUDA install refreshes apt only after an NVIDIA repo change."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = REPO_ROOT / "roles/llm_gpu_serving/tasks/install-cuda-toolkit.yml"


def test_cuda_cache_refresh_tracks_the_shared_repo_result_and_skips_safely():
    task = yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))[0]
    expression = task["ansible.builtin.apt"]["update_cache"]
    template = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(expression)

    assert yaml.safe_load(template.render(nvidia_gpu_guest_nvidia_repo={"changed": True})) is True
    assert yaml.safe_load(template.render(nvidia_gpu_guest_nvidia_repo={"changed": False})) is False
    assert yaml.safe_load(template.render(nvidia_gpu_guest_nvidia_repo={"skipped": True})) is False
    assert yaml.safe_load(template.render()) is False
