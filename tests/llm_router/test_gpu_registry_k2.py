"""Validate the K2 Horizon trial registry projection."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_k2_horizon_trial_uses_its_pinned_catalogued_artifact() -> None:
    registry = yaml.safe_load((REPO_ROOT / "llm-models.d/61-gpu-matrix.yml").read_text())
    profiles = registry["_llm_registry_gpu_matrix"]
    entry = next(item for item in profiles if item["profile"] == "small-k2-horizon")
    artifacts = yaml.safe_load(
        (REPO_ROOT / "llm-models.d/71-gpu-artifacts-k2-horizon.yml").read_text()
    )["_llm_model_artifacts_k2_horizon"]
    artifact = next(item for item in artifacts if item["artifact_id"] == entry["artifact_id"])

    assert entry["route_group"] == "small-trial-gpu"
    assert entry["api_port_key"] == "llm_fast_trial_api"
    assert entry["co_resident_only"] is True
    assert entry["catalog_profile"] == entry["profile"]
    assert entry["hints"]["speed"] == "quick"
    assert entry["hints"]["quality"] == "routine"
    assert artifact["revision"] == "7a75dbda05e3d440351543131ff3f7fa5687e879"
