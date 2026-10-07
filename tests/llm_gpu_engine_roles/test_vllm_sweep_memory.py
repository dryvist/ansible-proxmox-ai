"""Keep the prepared vLLM sweep under the card's memory budget."""

from __future__ import annotations

from math import floor
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
ARTIFACT_FILE = REPO_ROOT / "llm-models.d/67-gpu-pro6000-artifacts-nvfp4-sweep.yml"
PROFILE_FILE = REPO_ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml"
GIB = 2**30
GPU_MEMORY_GIB = 96
# Sanitized from the production vLLM unit's non-default-args journal output on
# 2026-10-07; paths, host and address are intentionally omitted.
TARGET_MEDIUM_A_ARGS = {
    "max_model_len": 196608,
    "max_num_seqs": 8,
    "gpu_memory_utilization": 0.88,
    "linear_backend": "b12x",
}


def test_qwen38_profile_matrix_covers_context_slots_and_kv_dtype():
    profiles = yaml.safe_load(PROFILE_FILE.read_text(encoding="utf-8"))["vllm_serving_profiles"]
    matrix = {
        (profiles[name]["max_model_len"], profiles[name]["max_num_seqs"], profiles[name]["kv_cache_dtype"])
        for name in profiles
        if name.startswith("qwen38-")
    }
    assert matrix == {
        (context, slots, dtype)
        for context in (16384, 65536)
        for slots in (1, 4, 8)
        for dtype in ("auto", "fp8")
    } | {
        (196608, slots, dtype)
        for slots in (1, 4)
        for dtype in ("auto", "fp8")
    }

    assert profiles["medium-a"]["max_num_seqs"] == 8
    assert (196608, 4, "auto") in matrix
    assert {
        key: profiles["medium-a"][key] for key in TARGET_MEDIUM_A_ARGS
    } == TARGET_MEDIUM_A_ARGS


def test_candidate_profiles_fit_the_96_gib_memory_screen_and_stay_inactive():
    profiles = yaml.safe_load(PROFILE_FILE.read_text(encoding="utf-8"))["vllm_serving_profiles"]
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]
    artifacts = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))["_llm_model_artifacts_nvfp4_sweep"]
    artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    entries_by_profile = {entry["profile"]: entry for entry in entries}
    candidate_profiles = {
        name for name, profile in profiles.items() if "memory_screen_reserve_gib" in profile
    }

    assert len(candidate_profiles) == 22
    assert candidate_profiles <= entries_by_profile.keys()

    for name in candidate_profiles:
        profile = profiles[name]
        entry = entries_by_profile[name]
        artifact = artifacts_by_id[entry["artifact_id"]]
        assert entry["enabled"] is False
        assert entry["servable"] is False
        assert entry["max_parallel_requests"] == 64
        assert entry["context_window"] + entry["max_output_tokens"] == profile["max_model_len"]

        dtype_scale = 0.5 if profile["kv_cache_dtype"] == "fp8" else 1.0
        assert profile["kv_cache_dtype"] in {"auto", "fp8"}
        pool_token_capacity = floor(
            (GPU_MEMORY_GIB * profile["gpu_memory_utilization"]
             - artifact["model_store_size_bytes"] / GIB
             - profile["memory_screen_reserve_gib"])
            * GIB
            / (artifact["kv_cache_bytes_per_token_bf16"] * dtype_scale)
        )
        kv_gib = (
            artifact["kv_cache_bytes_per_token_bf16"]
            * profile["max_model_len"]
            * dtype_scale
            / GIB
        )
        checkpoint_gib = artifact["model_store_size_bytes"] / GIB
        reserve_gib = profile["memory_screen_reserve_gib"]
        estimated_gib = checkpoint_gib + kv_gib + reserve_gib
        budget_gib = GPU_MEMORY_GIB * profile["gpu_memory_utilization"]
        assert profile["max_model_len"] <= pool_token_capacity, (
            name, profile["max_model_len"], pool_token_capacity
        )
        assert estimated_gib <= budget_gib, (name, estimated_gib, budget_gib, pool_token_capacity)


def test_sweep_artifact_sizes_match_the_hugging_face_reads():
    artifacts = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))[
        "_llm_model_artifacts_nvfp4_sweep"
    ]
    by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    assert {
        artifact_id: by_id[artifact_id]["model_store_size_bytes"]
        for artifact_id in (
            "qwen38-27b-nvfp4",
            "qwen36-35b-a3b-nvfp4",
            "nemotron-super-120b-a12b-nvfp4",
            "muse-glimmer-30b-nvfp4",
            "nemotron-lightning-30b-a3b-nvfp4",
        )
    } == {
        "qwen38-27b-nvfp4": 21945291730,
        "qwen36-35b-a3b-nvfp4": 23462477857,
        "nemotron-super-120b-a12b-nvfp4": 80365684262,
        "muse-glimmer-30b-nvfp4": 24698226650,
        "nemotron-lightning-30b-a3b-nvfp4": 21583785438,
    }
