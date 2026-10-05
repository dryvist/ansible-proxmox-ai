"""Validate the Pro6000 tier's registry-owned profile contract."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = REPO_ROOT / "llm-models.d/60-gpu-pro6000.yml"
ARTIFACT_FILE = REPO_ROOT / "llm-models.d/65-gpu-pro6000-artifacts.yml"
REGISTRY_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main/20-registry.yml"
SERVING_DEFAULTS = REPO_ROOT / "roles/llm_gpu_serving/defaults/main/10-profiles.yml"
SERVING_CORE_DEFAULTS = REPO_ROOT / "roles/llm_gpu_serving/defaults/main/00-core.yml"


def test_pro6000_profiles_are_inactive_placeholders_and_free() -> None:
    registry = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    entries = registry["_llm_registry_gpu_pro6000"]
    artifacts = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))["_llm_model_artifacts"]
    artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    defaults = yaml.safe_load(REGISTRY_DEFAULTS.read_text(encoding="utf-8"))
    serving_profiles = yaml.safe_load(SERVING_DEFAULTS.read_text(encoding="utf-8"))["llm_profiles"]
    serving_core = yaml.safe_load(SERVING_CORE_DEFAULTS.read_text(encoding="utf-8"))

    assert len(entries) == 4
    assert defaults["llm_router_gpu_profiles_enabled"] is False
    assert {entry["profile"] for entry in entries} == {"small", "medium-a", "medium-b", "max"}
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
        assert entry["input_cost_per_token"] == 0
        assert entry["output_cost_per_token"] == 0
        assert entry["max_parallel_requests"] == 1
        assert entry["num_retries"] == 0

    assert artifacts_by_id[entries[0]["artifact_id"]]["use"] == "serving"
    assert artifacts_by_id[entries[1]["artifact_id"]]["use"] == "serving"
    assert all(
        artifacts_by_id[entry["artifact_id"]]["use"] == "benchmark-only"
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
    }
    assert len({alias for aliases in aliases_by_profile.values() for alias in aliases}) == 2

    # The campaign's 27B cell config sets the same total vLLM context and
    # sequence count as the medium-a serving profile. The router registry
    # advertises the input remainder after its output reservation.
    qwen_profile = next(entry for entry in entries if entry["profile"] == "medium-a")
    qwen_artifact = artifacts_by_id[qwen_profile["artifact_id"]]
    medium_a_serving = serving_profiles[qwen_profile["profile"]]
    assert serving_core["llm_gpu_serving_vllm_version"] == "0.30.0"
    assert medium_a_serving["max_model_len"] == qwen_artifact["context_window_tokens"] == 196608
    assert medium_a_serving["max_num_seqs"] == 8
    assert qwen_profile["context_window"] + qwen_profile["max_output_tokens"] == medium_a_serving["max_model_len"]

    # Every registry input window plus its output reservation must fit both
    # the selected artifact and the profile's configured serving limit.
    for entry in entries:
        profile = serving_profiles[entry["profile"]]
        assert entry["context_window"] + entry["max_output_tokens"] <= profile["max_model_len"]
