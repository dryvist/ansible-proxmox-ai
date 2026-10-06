"""Per-model limits belong to the shared catalog, not llm-models.d slices."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_LIMIT_FIELDS = {
    "context_window",
    "max_output_tokens",
    "max_parallel_requests",
    "max_queue_size",
    "queue_size",
    "request_timeout",
    "stream_timeout",
    "num_retries",
    "allowed_fails",
    "cooldown_time",
}


def test_registry_slices_do_not_duplicate_catalog_numeric_limits() -> None:
    """All serving consumers load their per-model numeric values from contracts."""
    files = sorted((REPO_ROOT / "llm-models.d").glob("*.yml"))
    assert files
    duplicates = []
    for path in files:
        for entries in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).values():
            for entry in entries:
                if entry.get("enabled") is False:
                    continue
                repeated = MODEL_LIMIT_FIELDS.intersection(entry)
                if repeated:
                    duplicates.append(
                        f"{path.relative_to(REPO_ROOT)}:{entry['client_model_id']}: "
                        f"{', '.join(sorted(repeated))}"
                    )
    assert not duplicates, "Per-model catalog limits were duplicated in llm-models.d: " + "; ".join(duplicates)
