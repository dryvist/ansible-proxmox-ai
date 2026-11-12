"""Local registry limits fit their engines and the total context cap."""

import yaml

from test_gpu_model_info_profile import _projected_entries
from test_gpu_registry import (
    LEGACY_DEFAULTS,
    LLAMACPP_DEFAULTS,
    REPO_ROOT,
    SGLANG_DEFAULTS,
    VLLM_DEFAULTS,
    _serving_profiles,
)


MAX_LOCAL_CONTEXT_TOKENS = 200000


def test_local_registry_and_serving_profiles_fit_the_total_context_cap():
    local_tiers = yaml.safe_load(
        (REPO_ROOT / "roles/llm_router/defaults/main/59-key-scopes.yml").read_text(
            encoding="utf-8"
        )
    )["llm_router_local_registry_tiers"]
    profile_sources = (
        (VLLM_DEFAULTS, "vllm_serving_profiles_core"),
        (
            VLLM_DEFAULTS.with_name("11-profile-families.yml"),
            "vllm_serving_profiles_specialized",
        ),
        (LLAMACPP_DEFAULTS, "llamacpp_serving_profiles"),
        (SGLANG_DEFAULTS, "sglang_serving_profiles"),
        (LEGACY_DEFAULTS, "llm_profiles"),
    )
    for path, key in profile_sources:
        profiles = yaml.safe_load(path.read_text(encoding="utf-8"))[key]
        assert profiles, path
        for name, profile in profiles.items():
            assert 0 < profile["max_model_len"] <= MAX_LOCAL_CONTEXT_TOKENS, (path, name)

    serving_profiles = _serving_profiles()
    local_entries = [entry for entry in _projected_entries() if entry["tier"] in local_tiers]
    assert local_entries
    for entry in local_entries:
        engine_window = (
            serving_profiles[entry["profile"]]["max_model_len"]
            if "profile" in entry
            else MAX_LOCAL_CONTEXT_TOKENS
        )
        assert engine_window <= MAX_LOCAL_CONTEXT_TOKENS, entry["client_model_id"]
        if "context_window" in entry:
            output_budget = entry.get("max_output_tokens", entry["context_window"])
            assert entry["context_window"] + output_budget <= engine_window, (
                entry["client_model_id"]
            )
