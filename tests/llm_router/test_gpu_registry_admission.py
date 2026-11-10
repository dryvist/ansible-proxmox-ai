"""Validate GPU router admission caps against serving profile slots."""

from test_gpu_registry import _entries, _serving_profiles


def test_router_admission_supports_load_sweep_and_engine_slots() -> None:
    """Sweeps admit 1..64; every other entry admits its engine slot count."""
    entries = _entries()
    serving_profiles = _serving_profiles()
    admission_profiles = {
        entry["profile"]
        for entry in entries
        if entry["client_model_id"].startswith("gpu-sweep-")
    }

    caps = {
        entry["profile"]: entry.get(
            "max_parallel_requests",
            64 if entry["profile"] in admission_profiles
            else serving_profiles[entry["profile"]]["max_num_seqs"],
        )
        for entry in entries
    }
    expected_caps = {
        name: 64 if name in admission_profiles else serving_profiles[name]["max_num_seqs"]
        for name in caps
    }
    assert caps == expected_caps

    assert caps["medium-a"] == serving_profiles["medium-a"]["max_num_seqs"] == 8
    assert caps["qwen38-96k-11-fp8-mtp"] == 16
