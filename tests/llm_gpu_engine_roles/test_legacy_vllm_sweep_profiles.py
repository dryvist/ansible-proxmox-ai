"""Render registered vLLM sweep profiles through the legacy GPU service unit."""

from __future__ import annotations

import json

import jinja2
import yaml

from test_engine_profiles import (
    LEGACY_ROOT,
    VLLM_ROOT,
    _comment_filter,
    _exec_start,
    _registry_profiles,
)

TEST_API_PORT = "TEST_API_PORT"


def _vllm_profiles() -> dict:
    return yaml.safe_load(
        (VLLM_ROOT / "defaults/main/10-profiles.yml").read_text(encoding="utf-8")
    )["vllm_serving_profiles"]


def _sweep_profiles() -> dict:
    return {
        name: profile
        for name, profile in _vllm_profiles().items()
        if profile.get("engine") == "vllm"
        and profile.get("max_model_len") == 65536
        and profile.get("max_num_seqs") == 8
        and profile.get("kv_cache_dtype") == "fp8"
        and profile.get("quantization") == "modelopt"
        and profile.get("speculative_config", {}).get("num_speculative_tokens", 3) == 3
    }


def _render_legacy(profile_name: str, profile: dict) -> str:
    registry_model = _registry_profiles()[profile_name]
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(LEGACY_ROOT / "templates"),
        trim_blocks=True,
        lstrip_blocks=False,
        undefined=jinja2.StrictUndefined,
    )
    env.filters.update(
        {
            "bool": bool,
            "comment": _comment_filter,
            "mandatory": lambda value, message="": value,
            "to_json": json.dumps,
        }
    )
    values = {
        "ansible_managed": "Managed by Ansible",
        "ansible_facts": {"default_ipv4": {"address": "LISTEN_ADDRESS"}},
        "llm_gpu_serving_user": "llm-gpu-serving",
        "llm_gpu_serving_group": "llm-gpu-serving",
        "llm_gpu_serving_data_dir": "/var/lib/llm-gpu-serving",
        "llm_gpu_serving_hf_home": "HF_CACHE_HOME",
        "llm_gpu_serving_cuda_home": "/usr/local/cuda-X.Y",
        "llm_gpu_serving_venv": "/opt/llm-gpu-serving/venv",
        "llm_gpu_serving_listen_host": "LISTEN_ADDRESS",
        "llm_gpu_serving_vllm_prefix_caching_enabled": True,
        "llm_gpu_serving_profile_name": profile_name,
        "llm_gpu_serving_profile": {**profile, "port": TEST_API_PORT},
        "llm_gpu_serving_profile_registry_model": registry_model,
        "llm_gpu_serving_profile_model_dir": f"/cache/models/{registry_model['artifact']['hf_repo']}",
    }
    return env.get_template("llm-gpu-serving.service.j2").render(**values)


def test_legacy_loader_uses_the_vllm_definitions_for_the_selected_profile():
    tasks = yaml.safe_load((LEGACY_ROOT / "tasks/load-registry.yml").read_text(encoding="utf-8"))
    load = next(task for task in tasks if task.get("name") == "Load shared vLLM profile definitions")
    merge = next(task for task in tasks if task.get("name") == "Add the selected vLLM profile to legacy serving")

    assert "vllm_serving/defaults/main/10-profiles.yml" in load["ansible.builtin.include_vars"]["file"]
    defaults = yaml.safe_load((LEGACY_ROOT / "defaults/main/10-profiles.yml").read_text(encoding="utf-8"))
    assert defaults["llm_gpu_serving_profiles"] == "{{ llm_profiles }}"
    profile_map = merge["ansible.builtin.set_fact"]["llm_gpu_serving_profiles"]
    assert "vllm_serving_profiles[llm_active_profile]" in profile_map
    assert "llm_gpu_serving_api_port" in profile_map
    assert merge["vars"]["nvidia_gpu_guest_api_port"] == "{{ llm_gpu_serving_api_port }}"
    assert "llm_active_profile not in llm_gpu_serving_profiles" in merge["when"]
    assert any("llm_gpu_serving_registry_profiles" in condition for condition in merge["when"])


def test_selected_sweep_profiles_render_legacy_units_with_vllm_flags_and_paths():
    profiles = _sweep_profiles()
    registry_profiles = _registry_profiles()
    assert len(profiles) == 4

    for name, profile in profiles.items():
        if not profile.get("enabled", True):
            assert name not in registry_profiles or registry_profiles[name]["enabled"] is False
            continue

        unit = _render_legacy(name, profile)
        exec_start = _exec_start(unit)
        registry_model = registry_profiles[name]

        assert "--max-model-len 65536" in exec_start
        assert "--max-num-seqs 8" in exec_start
        assert "--kv-cache-dtype fp8" in exec_start
        assert "--quantization modelopt" in exec_start
        assert f"vllm serve /cache/models/{registry_model['artifact']['hf_repo']}" in exec_start
        assert f"--served-model-name {registry_model['upstream_model_id']}" in exec_start
        assert f"--port {TEST_API_PORT}" in exec_start

        if "speculative_config" in profile:
            rendered = json.loads(exec_start.split("--speculative-config '", 1)[1].split("'", 1)[0])
            assert rendered == {"method": "mtp", "num_speculative_tokens": 3}
        else:
            assert "--speculative-config" not in exec_start

        if profile.get("engram_config"):
            rendered_engram = json.loads(exec_start.split("--engram-config '", 1)[1].split("'", 1)[0])
            assert rendered_engram == profile["engram_config"]
        if profile.get("enable_flashinfer_autotune") is False:
            assert "--no-enable-flashinfer-autotune" in exec_start
        if registry_model["artifact"].get("trust_remote_code"):
            assert "--trust-remote-code" in exec_start


def test_qwen38_long_context_profiles_render_through_legacy_units():
    profiles = _vllm_profiles()
    expected = {
        "qwen38-128k-2-fp8-mtp": (131072, 2),
        "qwen38-128k-11-fp8-mtp": (131072, 11),
        "qwen38-256k-1-fp8-mtp": (262144, 1),
        "qwen38-256k-5-fp8-mtp": (262144, 5),
        "qwen38-64k-16-fp8-mtp": (65536, 16),
    }

    for name, (max_model_len, max_num_seqs) in expected.items():
        profile = profiles[name]
        exec_start = _exec_start(_render_legacy(name, profile))

        assert f"--max-model-len {max_model_len}" in exec_start
        assert f"--max-num-seqs {max_num_seqs}" in exec_start
        assert "--kv-cache-dtype fp8" in exec_start
        assert "--quantization modelopt" in exec_start
        rendered = json.loads(exec_start.split("--speculative-config '", 1)[1].split("'", 1)[0])
        assert rendered == {"method": "mtp", "num_speculative_tokens": 3}
