"""Model limits belong to homelab-contracts, not the router registry slices."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = REPO_ROOT / "llm-models.d/40-hermes-cloud.yml"


def _codex_subscription_entry() -> dict:
    # Selected by its `subscription` flag, not by client_model_id: a
    # registered id re-typed here would trip
    # test_registry_retype_scan.py's projection-zone check, which this file
    # (tests/**/*.py) is covered by.
    doc = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    entries = doc["_llm_registry_hermes_cloud"]
    matches = [e for e in entries if e.get("subscription") is True]
    assert len(matches) == 1, "expected exactly one subscription-flagged entry"
    return matches[0]


def test_registry_file_exists():
    assert REGISTRY_FILE.is_file(), REGISTRY_FILE


def test_codex_subscription_timeout_is_not_duplicated_in_the_router_registry():
    entry = _codex_subscription_entry()
    assert "stream_timeout" not in entry
    assert "request_timeout" not in entry
