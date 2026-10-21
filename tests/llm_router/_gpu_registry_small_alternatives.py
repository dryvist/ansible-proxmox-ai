"""Shared assertions for disabled small co-resident GPU alternatives."""

SMALL_CANDIDATES = {
    "small-lfm25-2b6": (
        "lfm25-2b6-q4-k-m",
        "LFM2.5-2.6B-Q4_K_M.gguf",
        1_674_455_040,
    ),
    "small-granite42-3b": (
        "granite42-3b-q4-k-m",
        "granite-4.2-3b-Q4_K_M.gguf",
        2_244_011_552,
    ),
    "small-lfm25-24b-a2b": (
        "lfm25-24b-a2b-q4-k-m",
        "LFM2.5-24B-A2B-Q4_K_M.gguf",
        14_526_258_464,
    ),
}


def assert_small_alternatives(
    entries: list[dict],
    artifacts_by_id: dict,
    serving_profiles: dict,
    co_resident_profile: dict,
) -> None:
    for profile_name, (artifact_id, gguf_file, size_bytes) in SMALL_CANDIDATES.items():
        entry = next(item for item in entries if item["profile"] == profile_name)
        artifact = artifacts_by_id[entry["artifact_id"]]
        profile = serving_profiles[profile_name]
        assert entry["client_model_id"].startswith("gpu-small-")
        assert entry["enabled"] is False
        assert entry["servable"] is False
        assert entry["route_group"] == "small"
        assert entry["co_resident_only"] is True
        assert entry["order"] == 1
        assert entry["context_window"] + entry["max_output_tokens"] == 65536
        assert artifact["artifact_id"] == artifact_id
        assert artifact["serving_profile"] == profile_name
        assert artifact["model_store_profile"] == profile_name
        assert artifact["model_store_size_bytes"] == size_bytes
        assert artifact["gguf_file"] == gguf_file
        assert artifact["use"] == "serving"
        assert profile["enabled"] is True
        assert profile["co_resident_only"] is True
        for key in (
            "engine",
            "max_model_len",
            "max_num_seqs",
            "flash_attention",
            "gpu_layers",
            "kv_cache_dtype",
            "gpu_memory_reserve_gib",
            "port",
        ):
            assert profile[key] == co_resident_profile[key]
