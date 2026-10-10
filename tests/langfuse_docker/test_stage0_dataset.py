"""Keep the public Stage 0 dataset subset free of private identifiers and costs."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_FILE = (
    REPO_ROOT / "roles/langfuse_docker/files/datasets/typed-decisions-stage0.json"
)


def test_stage0_dataset_subset_contains_no_private_identifiers_or_prices():
    dataset = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    rows = json.dumps(dataset["rows"], ensure_ascii=False).lower()
    forbidden_patterns = (
        r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])",
        r"(?<![\w@])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?![\w])",
        r"[$€£¥₹₩]",
        r"\b\d[\d,.]*\s?(?:usd|eur|gbp|dollars?|euros?|pounds?)\b",
        r"\b(?:receipt|invoice|transaction\s*(?:id|ref(?:erence)?)|payment\s*ref(?:erence)?)\b",
    )

    assert dataset["rows"]
    assert dataset["length"] == len(dataset["rows"])
    assert all(
        re.search(pattern, rows, re.IGNORECASE) is None
        for pattern in forbidden_patterns
    )
    for entry in dataset["rows"]:
        row = entry["row"]
        assert {"id", "workflow", "state", "questions", "gold"} <= row.keys()
        json.loads(row["state"])
        json.loads(row["questions"])
        json.loads(row["gold"])
