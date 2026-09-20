"""codex-subscription's stream_timeout must be the same base variable the
local rungs use (llm_router_admission_budget_seconds), not a second literal
copy of the number.

Parsed with yaml.safe_load, never a string grep, per the promotion review's
prescribed test shape (promotion-811-review.md finding 9): a grep for "15"
would pass on a coincidental literal that happens to match today's budget and
drift silently the day the budget changes. Only a parsed comparison against
the exact Jinja reference proves the SAME variable, not merely the same value.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILE = REPO_ROOT / "llm-models.d/40-hermes-cloud.yml"
EXPECTED = "{{ llm_router_admission_budget_seconds }}"


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


def test_codex_subscription_stream_timeout_uses_the_shared_admission_budget_var():
    entry = _codex_subscription_entry()
    assert entry["stream_timeout"] == EXPECTED, (
        f"stream_timeout must reference {EXPECTED!r} (the same base variable "
        f"the local rungs use, llm-models.d/30-local-gpu.yml), got "
        f"{entry['stream_timeout']!r}"
    )


def test_codex_subscription_stream_timeout_is_not_a_literal_or_folded_scalar():
    # The old form (`stream_timeout: 15`) parses as an int; a `>-` folded
    # block scalar would parse as a string but never equal EXPECTED exactly
    # (a trailing space/newline survives folding). Both are ruled out by the
    # exact-match assertion above; this restates the failure mode explicitly.
    entry = _codex_subscription_entry()
    assert isinstance(entry["stream_timeout"], str)
    assert entry["stream_timeout"].strip() == entry["stream_timeout"]
