"""Validate artifact registration as the source for GPU profile metadata."""

import re

import yaml

from test_engine_profiles import (
    LEGACY_ROOT,
    _artifacts,
    _registry_entries,
    _registry_profiles,
)
from test_sglang_profiles import _all_profiles


def test_artifact_registry_is_the_only_source_for_model_files_and_quantization():
    artifacts = _artifacts()
    artifact_ids = [artifact["artifact_id"] for artifact in artifacts]
    assert len(artifact_ids) == len(set(artifact_ids))

    required = {
        "artifact_id",
        "hf_repo",
        "include_globs",
        "format",
        "quantization",
        "engines",
        "use",
    }
    assert all(required <= artifact.keys() for artifact in artifacts)
    assert all(artifact["include_globs"] for artifact in artifacts)
    assert all(artifact["use"] in {"serving", "benchmark-only"} for artifact in artifacts)

    campaign_artifacts = [artifact for artifact in artifacts if "model_size" in artifact]
    assert all(artifact["model_task"] for artifact in campaign_artifacts)
    assert all(artifact["model_task_source"] in {"model_card", "inferred"} for artifact in campaign_artifacts)
    assert all("model_task" not in artifact or "model_task_source" in artifact for artifact in artifacts)

    registry_entries = _registry_entries()
    assert all("artifact_id" in entry for entry in registry_entries)
    assert all(
        not {"hf_repo", "include_globs", "format", "quantization"} & entry.keys()
        for entry in registry_entries
    )
    assert all(
        not {"quant", "gguf_file"} & profile.keys() for profile in _all_profiles().values()
    )

    profiles = _registry_profiles()
    assert profiles["small"]["artifact_id"] == "qwen35-9b-q4-k-m"
    assert all(model["artifact"]["use"] == "serving" for model in profiles.values())
    assert profiles["small"]["artifact"]["engines"] == ["llama_cpp"]
    assert profiles["medium-a"]["artifact"]["engines"] == ["vllm", "sglang"]
    assert profiles["medium-b"]["artifact"]["format"] == "GGUF"
    assert profiles["16gb"]["artifact"]["artifact_id"] == "qwen38-27b-ud-iq3-xxs"
    assert profiles["16gb"]["artifact"]["format"] == "GGUF"
    assert profiles["max"]["artifact"]["format"] == "GGUF"


def test_legacy_128k_eight_slot_profile_uses_the_registered_small_quant():
    defaults = yaml.safe_load(
        (LEGACY_ROOT / "defaults/main/10-profiles.yml").read_text(encoding="utf-8")
    )
    profile_name = "flash-next-small-128k-8"
    profile = defaults["llm_profiles"][profile_name]
    registered = _registry_profiles()[profile_name]
    artifact = registered["artifact"]

    assert (profile["max_model_len"], profile["max_num_seqs"]) == (131072, 8)
    assert profile["engine"] == "llama_cpp"
    assert profile["flash_attention"] is True
    assert profile["gpu_layers"] == 99
    assert profile["kv_cache_dtype"] == "q8_0"
    assert registered["artifact_id"] == "flash-next-unsloth-gguf"
    assert artifact["quantization"] == "UD-Q2_K_XL"
    assert re.fullmatch(r"[0-9a-f]{40}", artifact["revision"])


def test_best_liked_mid_flash_next_quant_is_revision_pinned():
    artifact = next(
        artifact
        for artifact in _artifacts()
        if artifact["artifact_id"] == "flash-next-unsloth-ud-q3-k-xl"
    )

    assert artifact["quantization"] == "UD-Q3_K_XL"
    assert artifact["include_globs"] == ["UD-Q3_K_XL/*"]
    assert re.fullmatch(r"[0-9a-f]{40}", artifact["revision"])
    assert artifact["use"] == "benchmark-only"
