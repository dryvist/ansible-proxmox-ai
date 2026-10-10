"""Keep llama.cpp release selection pinned, verified, and proxied."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RELEASE_ROOT = ROOT / "roles/llamacpp_release"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_one_renovate_pin_declares_every_backend_asset():
    defaults_path = RELEASE_ROOT / "defaults/main/00-release.yml"
    text = defaults_path.read_text(encoding="utf-8")
    defaults = _load(defaults_path)
    pin_line = next(
        index
        for index, line in enumerate(text.splitlines())
        if line.startswith("llamacpp_release_tag:")
    )

    assert text.splitlines()[pin_line - 1].startswith("# renovate: datasource=github-releases")
    assert r"depName=ggml-org/llama.cpp versioning=regex:^b(?<major>\d+)$" in text.splitlines()[pin_line - 1]
    for key in (
        "llamacpp_release_cpu_asset",
        "llamacpp_release_vulkan_asset",
        "llamacpp_release_rocm_asset",
        "llamacpp_release_cuda_asset",
        "llamacpp_release_cuda_runtime_asset",
    ):
        assert "{{ llamacpp_release_tag }}" in defaults[key]

    for key in (
        "llamacpp_release_cpu_sha256",
        "llamacpp_release_vulkan_sha256",
        "llamacpp_release_rocm_sha256",
        "llamacpp_release_cuda_sha256",
        "llamacpp_release_cuda_runtime_sha256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", defaults[key])


def test_shared_download_uses_the_pinned_checksum_and_requires_apt_proxy():
    defaults_path = RELEASE_ROOT / "defaults/main/00-release.yml"
    prepare = _load(RELEASE_ROOT / "tasks/prepare.yml")
    download = _load(RELEASE_ROOT / "tasks/download.yml")
    proxy_check = _load(RELEASE_ROOT / "tasks/require-proxy.yml")
    get_url = next(task["ansible.builtin.get_url"] for task in download if "ansible.builtin.get_url" in task)

    assert any("tasks/load.yml" in str(task) for task in prepare)
    assert any("require-proxy.yml" in str(task) for task in prepare)
    assert "llamacpp_release_proxy_urls | length > 0" in str(proxy_check)
    assert "cache_proxy_urls.apt_cache" in str(proxy_check)
    assert "APT_PROXY_URL" not in str(proxy_check)
    assert "cache_proxy_urls" in defaults_path.read_text(encoding="utf-8")
    assert get_url["url"] == "{{ llamacpp_release_download_url }}/{{ llamacpp_release_asset_name }}"
    assert get_url["checksum"] == "sha256:{{ llamacpp_release_asset_sha256 }}"
    assert download[-1]["environment"] == "{{ llamacpp_release_proxy_environment }}"


def test_all_three_installers_use_markers_and_never_query_release_api():
    cases = (
        ("roles/llm_gpu_serving/tasks/main.yml", "llm_gpu_serving_llamacpp_asset_marker"),
        ("roles/llamacpp_serving/tasks/install.yml", "llamacpp_serving_asset_marker"),
        ("roles/llama_cpp/tasks/main.yml", "llama_cpp_llamacpp_asset_marker"),
    )
    for relative_path, marker in cases:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "api.github.com/repos/ggml-org/llama.cpp" not in text
        assert "llamacpp_release/tasks/download.yml" in text
        assert marker in text

    installers = {
        "roles/llm_gpu_serving/tasks/main.yml": (
            "llamacpp_release_cuda_sha256",
            "llamacpp_release_cuda_runtime_sha256",
        ),
        "roles/llamacpp_serving/tasks/install.yml": (
            "llamacpp_release_cuda_sha256",
            "llamacpp_release_cuda_runtime_sha256",
        ),
        "roles/llama_cpp/defaults/main/00-core.yml": (
            "llamacpp_release_rocm_sha256",
            "llamacpp_release_vulkan_sha256",
            "llamacpp_release_cpu_sha256",
            "llama_cpp_llamacpp_asset_sha256",
        ),
        "roles/llama_cpp/tasks/main.yml": ("llama_cpp_llamacpp_asset_sha256",),
    }
    for relative_path, checksums in installers.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert all(checksum in text for checksum in checksums), relative_path


def test_package_and_model_fetch_tasks_use_the_shared_proxy():
    paths = (
        "roles/llm_gpu_serving/tasks/main.yml",
        "roles/llm_gpu_serving/tasks/install-cuda-toolkit.yml",
        "roles/llm_gpu_serving/tasks/install-nvidia-userspace.yml",
        "roles/llm_gpu_serving/tasks/cache-sync.yml",
        "roles/nvidia_gpu_guest/tasks/main.yml",
        "roles/nvidia_gpu_guest/tasks/install-cuda-toolkit.yml",
        "roles/nvidia_gpu_guest/tasks/install-nvidia-userspace.yml",
        "roles/nvidia_gpu_guest/tasks/cache-sync.yml",
        "roles/llama_cpp/tasks/main.yml",
    )
    for relative_path in paths:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert (
            "llamacpp_release_proxy" in text or "llamacpp_release/tasks" in text
        ), relative_path


def test_standalone_model_sync_entrypoints_load_shared_cache_settings():
    cache_sync_paths = (
        "roles/llm_gpu_serving/tasks/cache-sync.yml",
        "roles/nvidia_gpu_guest/tasks/cache-sync.yml",
    )
    for relative_path in cache_sync_paths:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "llamacpp_release/tasks/load.yml" in text, relative_path
        assert "llamacpp_release/tasks/require-proxy.yml" in text, relative_path

    verify_paths = ("roles/nvidia_gpu_guest/tasks/verify-model-store-origin-repo.yml",)
    for relative_path in verify_paths:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        tasks = _load(ROOT / relative_path)
        scripts = [task for task in tasks if "ansible.builtin.script" in task]
        assert len(scripts) == 1, relative_path
        script = scripts[0]
        command = script["ansible.builtin.script"]["cmd"]
        assert "verify-local-model-store.py" in command, relative_path
        assert "--revision" in command and "--include-globs" in command, relative_path
        assert script["changed_when"] is False, relative_path
        assert not any(
            key in task for task in tasks
            for key in ("ansible.builtin.command", "ansible.builtin.uri", "ansible.builtin.get_url")
        ), relative_path
        assert "llamacpp_release/tasks/prepare.yml" not in text, relative_path
