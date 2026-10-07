"""Keep the prepared vLLM sweep under the card's memory budget."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
ARTIFACT_FILE = REPO_ROOT / "llm-models.d/65-gpu-pro6000-artifacts.yml"
PROFILE_FILE = REPO_ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml"
GIB = 2**30
GPU_MEMORY_GIB = 96


def test_qwen38_profile_matrix_covers_context_slots_and_kv_dtype():
    profiles = yaml.safe_load(PROFILE_FILE.read_text(encoding="utf-8"))["vllm_serving_profiles"]
    matrix = {
        (profiles[name]["max_model_len"], profiles[name]["max_num_seqs"], profiles[name]["kv_cache_dtype"])
        for name in profiles
        if name == "medium-a" or name.startswith("qwen38-")
    }
    assert matrix == {
        (context, slots, dtype)
        for context in (16384, 65536, 196608)
        for slots in (1, 4)
        for dtype in ("auto", "fp8")
    }


def test_candidate_profiles_fit_the_96_gib_memory_screen_and_stay_inactive():
    profiles = yaml.safe_load(PROFILE_FILE.read_text(encoding="utf-8"))["vllm_serving_profiles"]
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]
    artifacts = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))["_llm_model_artifacts"]
    artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    entries_by_profile = {entry["profile"]: entry for entry in entries}
    candidate_profiles = {
        name for name, profile in profiles.items() if "memory_screen_reserve_gib" in profile
    }

    assert len(candidate_profiles) == 17
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
        kv_gib = (
            artifact["kv_cache_bytes_per_token_bf16"]
            * profile["max_model_len"]
            * profile["max_num_seqs"]
            * dtype_scale
            / GIB
        )
        checkpoint_gib = artifact["model_store_size_bytes"] / GIB
        reserve_gib = profile["memory_screen_reserve_gib"]
        estimated_gib = checkpoint_gib + kv_gib + reserve_gib
        budget_gib = GPU_MEMORY_GIB * profile["gpu_memory_utilization"]
        assert estimated_gib <= budget_gib, (name, estimated_gib, budget_gib)


def test_sweep_artifact_sizes_match_the_hugging_face_reads():
    artifacts = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))["_llm_model_artifacts"]
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
