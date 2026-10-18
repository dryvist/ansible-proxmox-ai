"""Validate the Pro6000 tier's registry-owned profile contract."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILES = (
    REPO_ROOT / "llm-models.d/60-gpu.yml",
    REPO_ROOT / "llm-models.d/61-gpu-matrix.yml",
)
ARTIFACT_FILES = tuple(sorted((REPO_ROOT / "llm-models.d").glob("*artifacts*.yml")))
REGISTRY_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main/20-registry.yml"
REGISTRY_TASKS = REPO_ROOT / "roles/llm_router/tasks/registry.yml"
VLLM_DEFAULTS = REPO_ROOT / "roles/vllm_serving/defaults/main/10-profiles.yml"
LLAMACPP_DEFAULTS = REPO_ROOT / "roles/llamacpp_serving/defaults/main/10-profiles.yml"
SGLANG_DEFAULTS = REPO_ROOT / "roles/sglang_serving/defaults/main/10-profiles.yml"
VLLM_CORE_DEFAULTS = REPO_ROOT / "roles/vllm_serving/defaults/main/00-engine.yml"
MATRIX_PROFILES = {
    "qwen38-64k-8-fp8-mtp0", "qwen38-64k-8-fp8-mtp2",
    "qwen38-64k-8-fp8-mtp4",
    "qwen38-128k-2-fp8-mtp", "qwen38-128k-11-fp8-mtp",
    "qwen38-256k-1-fp8-mtp", "qwen38-256k-5-fp8-mtp",
    "qwen38-256k-kvoff0", "qwen38-256k-kvoff24", "qwen38-256k-kvoffmax",
    "qwen38-64k-16-fp8-mtp",
    "flash-next-64k-8-fp8-mtp0", "flash-next-64k-8-fp8-mtp2", "flash-next-64k-8-fp8-mtp4",
    "flash-next-262k-1-fp8", "flash-next-262k-2-fp8",
    "nemotron-lightning-256k-8-fp8", "nemotron-lightning-1m-1-fp8",
    "nemotron-lightning-1m-2-fp8", "nemotron-super-vl-64k-8",
    "flash-next-gguf", "flash-next-gguf-262k-1", "flash-next-gguf-262k-2",
    "flash-next-small-128k-8",
    "nemotron-bolt-64k-16", "nemotron-bolt-256k-24",
}
RETIRED_FLASH_NEXT_NVFP4_PROFILES = {
    "flash-next-64k-1-fp8-mtp",
    "flash-next-64k-4-fp8-mtp",
    "flash-next-64k-1-fp8",
    "flash-next-64k-4-fp8",
    "flash-next-64k-8-fp8",
    "flash-next-64k-8-fp8-mtp0",
    "flash-next-64k-8-fp8-mtp2",
    "flash-next-64k-8-fp8-mtp4",
    "flash-next-262k-1-fp8",
    "flash-next-262k-2-fp8",
}
ENABLED_MATRIX_PROFILES = {"nemotron-bolt-64k-16", "nemotron-bolt-256k-24"}


def _entries() -> list[dict]:
    return [
        entry
        for path in REGISTRY_FILES
        for key, values in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_registry_gpu")
        for entry in values
    ]


GPU_CORE_DEFAULTS = REPO_ROOT / "roles/llm_gpu_serving/defaults/main/00-core.yml"
LEGACY_DEFAULTS = REPO_ROOT / "roles/llm_gpu_serving/defaults/main/10-profiles.yml"


def _serving_profiles() -> dict:
    vllm = yaml.safe_load(VLLM_DEFAULTS.read_text(encoding="utf-8"))["vllm_serving_profiles"]
    llamacpp = yaml.safe_load(LLAMACPP_DEFAULTS.read_text(encoding="utf-8"))["llamacpp_serving_profiles"]
    sglang = yaml.safe_load(SGLANG_DEFAULTS.read_text(encoding="utf-8"))["sglang_serving_profiles"]
    legacy = yaml.safe_load(LEGACY_DEFAULTS.read_text(encoding="utf-8"))["llm_profiles"]
    return {**legacy, **vllm, **llamacpp, **sglang}


def test_pro6000_profiles_are_guarded_and_free() -> None:
    entries = _entries()
    artifacts = [
        artifact
        for path in ARTIFACT_FILES
        for key, values in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_model_artifacts") or key.endswith("_artifacts")
        for artifact in values
    ]
    artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}
    defaults = yaml.safe_load(REGISTRY_DEFAULTS.read_text(encoding="utf-8"))
    serving_profiles = _serving_profiles()
    serving_core = yaml.safe_load(VLLM_CORE_DEFAULTS.read_text(encoding="utf-8"))
    gpu_core = yaml.safe_load(GPU_CORE_DEFAULTS.read_text(encoding="utf-8"))

    profiles = [entry["profile"] for entry in entries]
    assert profiles
    assert len(profiles) == len(set(profiles))
    assert set(profiles) <= set(serving_profiles)
    assert defaults["llm_router_gpu_profiles_enabled"] is False
    assert {entry["profile"] for entry in entries} == ({
        "small",
        "medium-a",
        "medium-b",
        "16gb",
        "qwen38-27b-131k-1",
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
        "qwen38-64k-8-fp8-mtp",
        "qwen38-64k-1-fp8-mtp",
        "qwen38-64k-4-fp8-mtp",
        "flash-next-64k-8-fp8-mtp",
        "flash-next-64k-1-fp8",
        "flash-next-64k-4-fp8",
        "flash-next-64k-8-fp8",
        "flash-next-64k-1-fp8-mtp",
        "flash-next-64k-4-fp8-mtp",
    } | MATRIX_PROFILES) - RETIRED_FLASH_NEXT_NVFP4_PROFILES
    assert all(not serving_profiles[name]["enabled"] for name in RETIRED_FLASH_NEXT_NVFP4_PROFILES)
    assert len({entry["client_model_id"] for entry in entries}) == len(entries)
    assert all(entry["artifact_id"] in artifacts_by_id for entry in entries)
    assert len(artifacts_by_id) == len(artifacts)
    assert all("upstream_model_id" not in entry for entry in entries)

    small = next(entry for entry in entries if entry["profile"] == "small")
    small_artifact = artifacts_by_id[small["artifact_id"]]
    assert small["artifact_id"] == "qwen35-9b-q4-k-m"
    assert small["enabled"] is False
    assert small["servable"] is False
    assert small["context_window"] == 6144
    assert small["max_output_tokens"] == 2048
    assert serving_profiles["small"]["engine"] == "llama_cpp"
    assert serving_profiles["small"]["max_model_len"] == 8192
    assert small_artifact["format"] == "GGUF"
    assert small_artifact["engines"] == ["llama_cpp"]
    assert small_artifact["gguf_file"] == "Qwen3.5-9B-Q4_K_M.gguf"

    for entry in entries:
        assert entry["provider"] == "hosted_vllm"
        assert entry["tier"] == "gpu"
        assert entry["enabled"] is (entry["profile"] in ENABLED_MATRIX_PROFILES)
        assert entry["servable"] is False
        if entry["profile"] in ENABLED_MATRIX_PROFILES:
            assert entry["lifecycle"] == "current"
            assert entry["catalog_profile"] == entry["profile"]
            assert all(
                arm in entry["notes"]
                for arm in (
                    'OFF {"enable_thinking":false}',
                    'default {"enable_thinking":true}',
                    'medium {"enable_thinking":true,"medium_effort":true}',
                )
            )
            continue
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
            if "catalog_profile" in entry:
                assert serving_profiles[entry["profile"]]["max_model_len"] <= model_window
            else:
                assert entry["context_window"] + entry["max_output_tokens"] <= model_window

    aliases_by_profile = {
        entry["profile"]: entry.get("stable_aliases", []) for entry in entries
    }
    assert all(not aliases for aliases in aliases_by_profile.values())
    assert {entry["profile"]: entry.get("route_group") for entry in entries} == {
        "small": "small",
        "medium-a": "medium",
        **{entry["profile"]: None for entry in entries if entry["profile"] not in {"small", "medium-a"}},
    }

    # medium-a stays the active eight-slot production profile, outside the sweep matrix.
    qwen_profile = next(entry for entry in entries if entry["profile"] == "medium-a")
    qwen_artifact = artifacts_by_id[qwen_profile["artifact_id"]]
    medium_a_serving = serving_profiles[qwen_profile["profile"]]
    assert serving_core["vllm_serving_version"] == gpu_core["llm_gpu_serving_vllm_version"]
    assert serving_core["vllm_serving_b12x_version"] == gpu_core["llm_gpu_serving_b12x_version"]
    assert medium_a_serving["max_model_len"] == 196608
    assert qwen_artifact["context_window_tokens"] == 262144
    assert medium_a_serving["max_num_seqs"] == 8
    assert medium_a_serving["kv_cache_dtype"] == "auto"
    assert qwen_profile["context_window"] + qwen_profile["max_output_tokens"] == medium_a_serving["max_model_len"]

    # Legacy rows carry their input and output windows; catalog rows use the
    # profile's complete model limit against the artifact context ceiling.
    for entry in entries:
        profile = serving_profiles[entry["profile"]]
        if "catalog_profile" in entry:
            assert profile["max_model_len"] <= artifacts_by_id[entry["artifact_id"]]["context_window_tokens"]
        else:
            assert entry["context_window"] + entry["max_output_tokens"] <= profile["max_model_len"]


def test_router_admission_supports_load_sweep_and_engine_slots() -> None:
    """Sweeps and medium-a admit 1..64; engine slots control active work."""
    entries = _entries()
    serving_profiles = _serving_profiles()

    caps = {
        entry["profile"]: entry.get("max_parallel_requests", 64)
        for entry in entries
    }
    admission_profiles = {
        entry["profile"]
        for entry in entries
        if entry["client_model_id"].startswith("gpu-sweep-")
    } | {"medium-a"}
    expected_caps = {
        name: 64 if name in admission_profiles else serving_profiles[name]["max_num_seqs"]
        for name in caps
    }
    assert caps == expected_caps

    assert caps["medium-a"] == 64


def test_router_consumes_the_shared_gpu_profile_projection() -> None:
    defaults = yaml.safe_load(REGISTRY_DEFAULTS.read_text(encoding="utf-8"))
    tasks = yaml.safe_load(REGISTRY_TASKS.read_text(encoding="utf-8"))
    projection = next(
        task["ansible.builtin.set_fact"]
        for task in tasks
        if task.get("name") == "Resolve GPU router upstream ids from artifact references"
    )

    assert "llm_router_gpu_profile_registry" in projection
    assert "llm_router_registry_gpu_profiles" in projection["llm_router_gpu_profile_registry"]
    assert "llm_router_gpu_profile_registry" in defaults["llm_router_model_registry"]
