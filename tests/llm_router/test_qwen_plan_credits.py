"""Qwen Token Plan metadata stays in credit units, separate from USD rates."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "llm-models.d/40-hermes-cloud.yml"
TOKEN_COST_FIELDS = {"input_cost_per_token", "output_cost_per_token"}


def _contains_key(value: object, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(_contains_key(item, target) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, target) for item in value)
    return False


def test_qwen_plan_credit_metadata_has_no_dollar_token_rates() -> None:
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = [
        entry
        for entry in registry["_llm_registry_hermes_cloud"]
        if entry.get("credential_env") == "QWEN_API_TOKEN"
    ]

    assert len(entries) == 3
    for entry in entries:
        model_info = entry["model_info"]
        assert model_info["plan_credit_unit"] == "plan_credits"
        assert "plan_credit_multiplier_off_peak" in model_info
        assert model_info["plan_off_peak_window_utc"] == "14:00-24:00"
        assert not any(_contains_key(entry, field) for field in TOKEN_COST_FIELDS)
