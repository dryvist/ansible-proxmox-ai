"""Check requested speculative, context, and sampling profile dimensions."""

from pathlib import Path

import yaml

from test_engine_profiles import _artifacts

ROOT = Path(__file__).resolve().parents[2]
VLLM_PROFILES = yaml.safe_load(
    (ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml").read_text()
)["vllm_serving_profiles"]
GPU_MATRIX = yaml.safe_load((ROOT / "llm-models.d/61-gpu-matrix.yml").read_text())[
    "_llm_registry_gpu_matrix"
]


def test_nvfp4_mtp_profiles_cover_requested_draft_counts():
    for prefix, expected in (
        ("qwen38-64k-8-fp8-mtp", {0, 2, 3, 4}),
        ("flash-next-64k-8-fp8-mtp", {0, 2, 3, 4}),
    ):
        arms = {
            profile.get("speculative_config", {}).get("num_speculative_tokens", 0)
            for name, profile in VLLM_PROFILES.items()
            if name.startswith(prefix)
        }
        assert arms == expected


def test_long_context_profiles_match_card_limits_and_memory_targets():
    qwen_mtp = VLLM_PROFILES["qwen38-64k-8-fp8-mtp"]
    qwen_profiles = {
        name: VLLM_PROFILES[name]
        for name in (
            "qwen38-128k-2-fp8-mtp",
            "qwen38-128k-11-fp8-mtp",
            "qwen38-256k-1-fp8-mtp",
            "qwen38-256k-5-fp8-mtp",
        )
    }
    flash = [
        profile
        for name, profile in VLLM_PROFILES.items()
        if name.startswith("flash-next-262k-")
    ]
    lightning = [
        profile
        for name, profile in VLLM_PROFILES.items()
        if name.startswith("nemotron-lightning-1m-")
    ]
    assert {(profile["max_model_len"], profile["max_num_seqs"]) for profile in flash} == {
        (262144, 1),
        (262144, 2),
    }
    assert all("hf_overrides" not in profile and not profile.get("allow_long_max_model_len") for profile in flash)
    assert {(profile["max_model_len"], profile["max_num_seqs"]) for profile in lightning} == {
        (1000000, 1),
        (1000000, 2),
    }
    assert all("hf_overrides" not in profile for profile in lightning)
    lightning_8 = VLLM_PROFILES["nemotron-lightning-256k-8-fp8"]
    assert (lightning_8["max_model_len"], lightning_8["max_num_seqs"]) == (262144, 8)
    assert lightning_8["reasoning_parser"] == "nemotron_v3"
    assert lightning_8["sampling_params"] == {"temperature": 1.0, "top_p": 0.95}
    super_vl = VLLM_PROFILES["nemotron-super-vl-64k-8"]
    assert (super_vl["max_model_len"], super_vl["max_num_seqs"]) == (65536, 8)
    assert super_vl["minimum_engine_version"] == "0.32.0"

    assert {
        (profile["max_model_len"], profile["max_num_seqs"])
        for profile in (*qwen_profiles.values(), VLLM_PROFILES["qwen38-64k-16-fp8-mtp"])
    } == {(131072, 2), (131072, 11), (262144, 1), (262144, 5), (65536, 16)}
    for profile in (*qwen_profiles.values(), VLLM_PROFILES["qwen38-64k-16-fp8-mtp"]):
        assert profile["gpu_memory_utilization"] == qwen_mtp["gpu_memory_utilization"]
        assert profile["quantization"] == qwen_mtp["quantization"] == "modelopt"
        assert profile["kv_cache_dtype"] == qwen_mtp["kv_cache_dtype"] == "fp8"
        assert profile["speculative_config"] == qwen_mtp["speculative_config"]
        assert "hf_overrides" not in profile and not profile.get("allow_long_max_model_len")

    qwen_matrix = {entry["profile"]: entry for entry in GPU_MATRIX}
    for name, profile in {
        **qwen_profiles,
        "qwen38-64k-16-fp8-mtp": VLLM_PROFILES["qwen38-64k-16-fp8-mtp"],
    }.items():
        entry = qwen_matrix[name]
        assert entry["enabled"] is False
        assert entry["servable"] is False
        assert entry["context_window"] == profile["max_model_len"] - 4096
        assert entry["max_output_tokens"] == 4096


def test_matrix_profiles_stay_within_the_registered_artifact_context():
    artifacts = {artifact["artifact_id"]: artifact for artifact in _artifacts()}
    llama_profiles = yaml.safe_load(
        (ROOT / "roles/llamacpp_serving/defaults/main/10-profiles.yml").read_text()
    )["llamacpp_serving_profiles"]
    legacy_profiles = yaml.safe_load(
        (ROOT / "roles/llm_gpu_serving/defaults/main/10-profiles.yml").read_text()
    )["llm_profiles"]

    for entry in GPU_MATRIX:
        artifact = artifacts[entry["artifact_id"]]
        if "catalog_profile" in entry:
            assert legacy_profiles[entry["profile"]]["max_model_len"] <= artifact["context_window_tokens"]
        else:
            assert entry["context_window"] + entry["max_output_tokens"] <= artifact["context_window_tokens"]
        for profiles in (VLLM_PROFILES, llama_profiles, legacy_profiles):
            if entry["profile"] in profiles:
                assert profiles[entry["profile"]]["max_model_len"] <= artifact["context_window_tokens"]


def test_radixark_flash_next_artifact_is_pinned_for_sglang_without_a_profile():
    artifact = next(
        artifact
        for artifact in _artifacts()
        if artifact["artifact_id"] == "flash-next-nvfp4-radixark"
    )

    assert artifact["revision"] == "7b719225242aacd3dbd3f9407468c2ee9a9d2594"
    assert artifact["engines"] == ["sglang"]
    assert "serving_profile" not in artifact
    assert all(profile["artifact_id"] != artifact["artifact_id"] for profile in GPU_MATRIX)
