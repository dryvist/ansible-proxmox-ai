"""Check the pinned llama.cpp CUDA asset and its matching runtime archive."""

from __future__ import annotations

from pathlib import Path

import yaml

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles/llamacpp_serving"
CORE_DEFAULTS = ROLE_ROOT / "defaults/main/00-engine.yml"
TASKS = ROLE_ROOT / "tasks/install.yml"
RELEASE_DEFAULTS = Path(__file__).resolve().parents[2] / "roles/llamacpp_release/defaults/main/00-release.yml"


def _release_defaults() -> dict:
    defaults = yaml.safe_load(RELEASE_DEFAULTS.read_text(encoding="utf-8"))
    tag = defaults["llamacpp_release_tag"]
    return {
        key: value.replace("{{ llamacpp_release_tag }}", tag)
        if isinstance(value, str)
        else value
        for key, value in defaults.items()
    }


def test_selects_the_pinned_cuda_binary_archive() -> None:
    defaults = _release_defaults()
    role_defaults = yaml.safe_load(CORE_DEFAULTS.read_text(encoding="utf-8"))
    expected_asset = f"llama-{defaults['llamacpp_release_tag']}-bin-ubuntu-cuda-13.4-x64.tar.gz"

    assert role_defaults["llamacpp_serving_asset_name"] == "{{ llamacpp_release_cuda_asset }}"
    assert defaults["llamacpp_release_cuda_asset"] == expected_asset


def test_pinned_runtime_asset_matches_the_binary_release() -> None:
    defaults = _release_defaults()
    assert defaults["llamacpp_release_cuda_runtime_asset"] == f"cudart-{defaults['llamacpp_release_cuda_asset']}"


def test_role_installs_the_runtime_archive_beside_the_binary() -> None:
    tasks = TASKS.read_text(encoding="utf-8")
    assert "llamacpp_release_cuda_runtime_asset" in CORE_DEFAULTS.read_text(encoding="utf-8")
    assert "llamacpp_release/tasks/download.yml" in tasks
    assert "llamacpp_serving_cudart_asset_name" in tasks
    assert "llamacpp_serving_cudart_find.files[0].path | dirname" in tasks


def test_install_replaces_a_running_binary() -> None:
    assert "--remove-destination" in TASKS.read_text(encoding="utf-8")


def test_role_reinstalls_when_the_recorded_asset_differs() -> None:
    tasks = TASKS.read_text(encoding="utf-8")
    assert "llamacpp_serving_asset_marker" in tasks
    assert "!= llamacpp_serving_asset_name" in tasks
