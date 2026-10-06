"""Check the llama.cpp asset choice: the binary archive, never its cudart- runtime twin, and the twin still pairs."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles/llm_gpu_serving"
CORE_DEFAULTS = ROLE_ROOT / "defaults/main/00-core.yml"
TASKS = ROLE_ROOT / "tasks/main.yml"

# Release asset names in the order the release API lists them: the runtime-only
# archive comes first, which is what the old pattern picked.
RELEASE_ASSETS = [
    "cudart-llama-b11457-bin-ubuntu-cuda-12.8-x64.tar.gz",
    "cudart-llama-b11457-bin-ubuntu-cuda-13.4-arm64.tar.gz",
    "cudart-llama-b11457-bin-ubuntu-cuda-13.4-x64.tar.gz",
    "llama-b11457-bin-ubuntu-arm64.tar.gz",
    "llama-b11457-bin-ubuntu-cuda-12.8-x64.tar.gz",
    "llama-b11457-bin-ubuntu-cuda-13.4-x64.tar.gz",
    "llama-b11457-bin-ubuntu-vulkan-x64.tar.gz",
]


def _selected() -> str:
    pattern = yaml.safe_load(CORE_DEFAULTS.read_text(encoding="utf-8"))["llm_gpu_serving_llamacpp_asset_regex"]
    return next(name for name in RELEASE_ASSETS if re.match(pattern, name))


def test_selects_the_binary_archive_not_the_runtime_archive() -> None:
    assert _selected() == "llama-b11457-bin-ubuntu-cuda-13.4-x64.tar.gz"


def test_runtime_twin_of_the_selection_is_a_release_asset() -> None:
    assert "cudart-" + _selected() in RELEASE_ASSETS


def test_role_installs_the_runtime_archive_beside_the_binary() -> None:
    tasks = TASKS.read_text(encoding="utf-8")
    assert "'/cudart-'" in tasks
    assert "llm_gpu_serving_llamacpp_cudart_find.files[0].path | dirname" in tasks


def test_install_replaces_a_running_binary() -> None:
    assert "--remove-destination" in TASKS.read_text(encoding="utf-8")


def test_role_reinstalls_when_the_recorded_asset_differs() -> None:
    tasks = TASKS.read_text(encoding="utf-8")
    assert "llm_gpu_serving_llamacpp_asset_url | basename" in tasks
    assert "llm_gpu_serving_llamacpp_asset_marker" in tasks
