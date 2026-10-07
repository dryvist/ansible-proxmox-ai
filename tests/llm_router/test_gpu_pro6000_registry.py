"""Validate the Pro6000 tier's registry-owned profile contract."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
ARTIFACT_FILES = (
    REPO_ROOT / "llm-models.d/65-gpu-pro6000-artifacts.yml",
    REPO_ROOT / "llm-models.d/66-gpu-pro6000-artifacts-glm53flash.yml",
    REPO_ROOT / "llm-models.d/67-gpu-pro6000-artifacts-nvfp4-sweep.yml",
)
REGISTRY_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main/20-registry.yml"
VLLM_DEFAULTS = REPO_ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml"
LLAMACPP_DEFAULTS = REPO_ROOT / "roles/llamacpp_serving/defaults/main/10-profiles.yml"
VLLM_CORE_DEFAULTS = REPO_ROOT / "roles/vllm_serving/defaults/main/00-engine.yml"


def _serving_profiles() -> dict:
    vllm = yaml.safe_load(VLLM_DEFAULTS.read_text(encoding="utf-8"))["vllm_serving_profiles"]
    llamacpp = yaml.safe_load(LLAMACPP_DEFAULTS.read_text(encoding="utf-8"))["llamacpp_serving_profiles"]
    return {**vllm, **llamacpp}


def test_pro6000_profiles_are_inactive_placeholders_and_free() -> None:
    registry = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    entries = registry["_llm_registry_gpu_pro6000"]
    artifacts = [
        artifact
        for path in ARTIFACT_FILES
        for key, values in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_model_artifacts")
        for artifact in values
    ]
    artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    defaults = yaml.safe_load(REGISTRY_DEFAULTS.read_text(encoding="utf-8"))
    serving_profiles = _serving_profiles()
    serving_core = yaml.safe_load(VLLM_CORE_DEFAULTS.read_text(encoding="utf-8"))

    assert len(entries) == 30
    assert defaults["llm_router_gpu_profiles_enabled"] is False
    assert {entry["profile"] for entry in entries} == {
        "small",
        "medium-a",
        "medium-b",
        "max",
        "glm-flash",
        "qwen38-16k-1-auto",
        "qwen38-16k-4-auto",
        "qwen38-16k-1-fp8",
        "qwen38-16k-4-fp8",
        "qwen38-64k-1-auto",
        "qwen38-64k-1-fp8",
        "qwen38-64k-4-auto",
        "qwen38-64k-4-fp8",
        "qwen38-192k-1-auto",
        "qwen38-192k-1-fp8",
        "qwen38-192k-4-fp8",
        "qwen38-196k-4-auto",
        "qwen38-262k-1-auto",
        "qwen38-262k-1-fp8",
        "qwen38-262k-2-auto",
        "qwen38-262k-2-fp8",
        "qwen38-16k-8-auto",
        "qwen38-16k-8-fp8",
        "qwen38-64k-8-auto",
        "qwen38-64k-8-fp8",
        "qwen36-35b-a3b",
        "nemotron-super-1x8192",
        "nemotron-super-2x4096",
        "muse-glimmer-30b",
        "nemotron-lightning-30b-a3b",
    }
    assert len({entry["client_model_id"] for entry in entries}) == len(entries)
    assert all(entry["artifact_id"] in artifacts_by_id for entry in entries)
    assert len(artifacts_by_id) == len(artifacts)
    assert all("upstream_model_id" not in entry for entry in entries)

    for entry in entries:
        assert entry["provider"] == "openai"
        assert entry["tier"] == "gpu"
        assert entry["enabled"] is False
        assert entry["servable"] is False
        assert isinstance(entry["context_window"], int) and entry["context_window"] > 0
        assert isinstance(entry["max_output_tokens"], int) and entry["max_output_tokens"] > 0
        assert entry.get("input_cost_per_token", 0) == 0
        assert entry.get("output_cost_per_token", 0) == 0
        assert isinstance(entry["max_parallel_requests"], int) and entry["max_parallel_requests"] > 0
        assert entry["num_retries"] == 0

    assert artifacts_by_id[entries[0]["artifact_id"]]["use"] == "serving"
    assert artifacts_by_id[entries[1]["artifact_id"]]["use"] == "serving"
    assert all(
        artifacts_by_id[entry["artifact_id"]]["use"] == "serving"
        for entry in entries[2:]
    )
    for entry in entries:
        model_window = artifacts_by_id[entry["artifact_id"]].get("context_window_tokens")
        if model_window is not None:
            assert entry["context_window"] + entry["max_output_tokens"] <= model_window

    aliases_by_profile = {
        entry["profile"]: entry.get("stable_aliases", []) for entry in entries
    }
    assert {profile: len(aliases) for profile, aliases in aliases_by_profile.items()} == {
        "small": 1,
        "medium-a": 1,
        "medium-b": 0,
        "max": 0,
        "glm-flash": 0,
        "qwen38-16k-1-auto": 0,
        "qwen38-16k-4-auto": 0,
        "qwen38-16k-1-fp8": 0,
        "qwen38-16k-4-fp8": 0,
        "qwen38-64k-1-auto": 0,
        "qwen38-64k-1-fp8": 0,
        "qwen38-64k-4-auto": 0,
        "qwen38-64k-4-fp8": 0,
        "qwen38-192k-1-auto": 0,
        "qwen38-192k-1-fp8": 0,
        "qwen38-192k-4-fp8": 0,
        "qwen38-196k-4-auto": 0,
        "qwen38-262k-1-auto": 0,
        "qwen38-262k-1-fp8": 0,
        "qwen38-262k-2-auto": 0,
        "qwen38-262k-2-fp8": 0,
        "qwen38-16k-8-auto": 0,
        "qwen38-16k-8-fp8": 0,
        "qwen38-64k-8-auto": 0,
        "qwen38-64k-8-fp8": 0,
        "qwen36-35b-a3b": 0,
        "nemotron-super-1x8192": 0,
        "nemotron-super-2x4096": 0,
        "muse-glimmer-30b": 0,
        "nemotron-lightning-30b-a3b": 0,
    }
    assert len({alias for aliases in aliases_by_profile.values() for alias in aliases}) == 2

    # medium-a stays the active eight-slot production profile, outside the sweep matrix.
    qwen_profile = next(entry for entry in entries if entry["profile"] == "medium-a")
    qwen_artifact = artifacts_by_id[qwen_profile["artifact_id"]]
    medium_a_serving = serving_profiles[qwen_profile["profile"]]
    assert serving_core["vllm_serving_version"] == "0.30.0"
    assert medium_a_serving["max_model_len"] == 196608
    assert qwen_artifact["context_window_tokens"] == 262144
    assert medium_a_serving["max_num_seqs"] == 8
    assert medium_a_serving["kv_cache_dtype"] == "auto"
    assert qwen_profile["context_window"] + qwen_profile["max_output_tokens"] == medium_a_serving["max_model_len"]

    # Every registry input window plus its output reservation must fit both
    # the selected artifact and the profile's configured serving limit.
    for entry in entries:
        profile = serving_profiles[entry["profile"]]
        assert entry["context_window"] + entry["max_output_tokens"] <= profile["max_model_len"]


def test_router_admission_supports_load_sweep_and_engine_slots() -> None:
    """Load sweep entries admit 1..64; vLLM max_num_seqs controls active slots."""
    entries = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))["_llm_registry_gpu_pro6000"]
    serving_profiles = _serving_profiles()

    caps = {entry["profile"]: entry["max_parallel_requests"] for entry in entries}
    sweep_profiles = {
        entry["profile"]
        for entry in entries
        if entry["client_model_id"].startswith("gpu-sweep-")
    } | {"medium-a"}
    expected_caps = {
        name: 64 if name in sweep_profiles else profile["max_num_seqs"]
        for name, profile in serving_profiles.items()
    }
    assert caps == expected_caps

    primary = [name for name, profile in serving_profiles.items() if profile.get("primary")]
    assert primary == ["medium-a"]
    assert caps["medium-a"] == 64
