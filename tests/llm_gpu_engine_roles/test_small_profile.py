"""Check the small GPU profile: llama.cpp slots, the pinned GGUF artifact, and vLLM-only installs."""

from __future__ import annotations

import yaml
from test_engine_profiles import (
    LEGACY_ROOT,
    _artifacts,
    _exec_start,
    _profiles,
    _registry_profiles,
    _render,
)
from test_legacy_vllm_sweep_profiles import _render_legacy


def test_small_profile_is_a_four_slot_llama_cpp_profile():
    small = _profiles()["small"]
    assert small["engine"] == "llama_cpp"
    assert small["max_model_len"] == 8192
    assert small["max_num_seqs"] == 4
    assert small["flash_attention"] is True
    assert small["gpu_layers"] == 99


def test_small_artifact_is_the_pinned_gguf_and_the_nvfp4_artifact_is_benchmark_only():
    small_artifact = _registry_profiles()["small"]["artifact"]
    assert small_artifact["revision"] == "3885219b6810b007914f3a7950a8d1b469d598a5"
    assert small_artifact["include_globs"] == ["Qwen3.5-9B-Q4_K_M.gguf"]
    assert small_artifact["format"] == "GGUF"
    assert small_artifact["quantization"] == "Q4_K_M"
    assert small_artifact["gguf_file"] == "Qwen3.5-9B-Q4_K_M.gguf"
    assert small_artifact["serving_profile"] == "small"
    assert small_artifact["model_store"] is True
    assert small_artifact["model_store_profile"] == "small"
    assert small_artifact["model_store_size_bytes"] == 5680522464

    old_nvfp4 = next(
        artifact for artifact in _artifacts() if artifact["artifact_id"] == "qwen35-9b-nvfp4"
    )
    assert old_nvfp4["use"] == "benchmark-only"
    assert old_nvfp4["model_store"] is False
    assert "serving_profile" not in old_nvfp4


def test_k2_horizon_is_a_pinned_high_reasoning_trial_profile():
    entry = _registry_profiles()["small-k2-horizon"]
    artifact = entry["artifact"]
    profile = yaml.safe_load(
        (LEGACY_ROOT / "defaults/main/10-profiles.yml").read_text(encoding="utf-8")
    )["llm_profiles"]["small-k2-horizon"]
    exec_start = " ".join(_exec_start(_render_legacy("small-k2-horizon", profile)).split())

    assert entry["route_group"] == "small-trial-gpu"
    assert entry["api_port_key"] == "llm_fast_trial_api"
    assert entry["catalog_profile"] == entry["profile"]
    assert artifact["revision"] == "7a75dbda05e3d440351543131ff3f7fa5687e879"
    assert artifact["gguf_file"] == "K2-Horizon-7B-Q4_K_M.gguf"
    assert artifact["quantization"] == "Q4_K_M"
    assert artifact["model_store_size_bytes"] == 5592217984
    assert profile["engine"] == "llama_cpp"
    assert profile["max_num_seqs"] == 4
    assert profile["max_model_len"] == 65536
    assert profile["gpu_memory_reserve_gib"] == 20
    assert "--reasoning-effort high" in exec_start
    assert "--parallel 4" in exec_start


def test_selected_registry_api_port_keys_override_profile_fallback_ports():
    tasks = yaml.safe_load((LEGACY_ROOT / "tasks/load-registry.yml").read_text(encoding="utf-8"))
    resolve = next(
        task for task in tasks
        if task["name"] == "Resolve selected profile ports from the shared service-port registry"
    )

    assert "api_port_key" in resolve["ansible.builtin.set_fact"]["llm_gpu_serving_profiles"]
    assert "tofu_data.constants.service_ports[profile.api_port_key]" in resolve[
        "ansible.builtin.set_fact"
    ]["llm_gpu_serving_profiles"]


def test_small_profile_renders_the_pinned_gguf_with_llama_cpp():
    artifact = _registry_profiles()["small"]["artifact"]
    exec_start = _exec_start(_render("small", _profiles()["small"]))

    assert f"/cache/models/{artifact['hf_repo']}/{artifact['gguf_file']}" in exec_start
    assert "--ctx-size 32768" in exec_start
    assert "--parallel 4" in exec_start
    assert "--n-gpu-layers 99" in exec_start
    assert "--flash-attn on" in exec_start
    assert "--jinja" in exec_start
    assert "vllm serve" not in exec_start
    assert "--tool-call-parser" not in exec_start


def test_legacy_role_installs_vllm_and_its_toolchain_only_for_vllm_profiles():
    tasks = yaml.safe_load((LEGACY_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    by_name = {task["name"]: task for task in tasks}
    vllm_engine = "llm_gpu_serving_profiles[llm_active_profile].engine == 'vllm'"

    assert by_name["Install vLLM CUDA helper build prerequisites"]["when"] == vllm_engine
    assert by_name["Install the CUDA toolkit for runtime kernel compilation"]["when"] == vllm_engine
    assert by_name["Create the vLLM virtual environment"]["when"] == vllm_engine
    assert vllm_engine in by_name["Install the pinned vLLM build with SM120 b12x kernels"]["when"]
    assert vllm_engine in by_name["Put the vLLM and Hugging Face CLIs on the default PATH"]["when"]


def test_vllm_venv_ownership_is_reset_before_the_install():
    tasks = yaml.safe_load((LEGACY_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    names = [task.get("name") for task in tasks]
    owner = tasks[names.index("Keep the vLLM virtual environment owned by the serving user")]

    assert owner["ansible.builtin.file"]["recurse"] is True
    assert owner["ansible.builtin.file"]["owner"] == "{{ llm_gpu_serving_user }}"
    assert names.index(owner["name"]) < names.index("Install the pinned vLLM build with SM120 b12x kernels")
