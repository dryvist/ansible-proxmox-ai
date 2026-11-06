"""Keep the large Studio route agent-scoped and catalog-driven."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "mlx-community/gpt-oss-120b-MXFP4-Q8"


def test_gpt_oss_is_an_agent_only_large_studio_route() -> None:
    registry = yaml.safe_load(
        (REPO_ROOT / "llm-models.d/10-large.yml").read_text(encoding="utf-8")
    )
    entries = [
        entry
        for entry in registry["_llm_registry_large"]
        if entry.get("client_model_id") == MODEL_ID
    ]

    assert len(entries) == 1
    entry = entries[0]
    assert entry["enabled"] is True
    assert entry["servable"] is True
    assert entry["tier"] == "local-large"
    assert entry["serving_role"] == "large"
    assert entry["route_group"] == "large-studio"
    assert entry["tags"] == ["agent-only"]
    assert entry["hints"]["best_for"] == ["planning", "review", "reasoning", "long-context"]
    assert not {
        "context_window",
        "max_output_tokens",
        "max_parallel_requests",
    }.intersection(entry)
