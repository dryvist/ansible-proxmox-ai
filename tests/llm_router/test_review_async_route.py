"""The review-async route group is Mac-first, and the medium group is unchanged.

Reads the registry source directly. The active GPU profile renders; other GPU
profiles do not. Asserts that:

* medium still holds the active GPU deployment (order 1) and the Mac deployment (order 2);
* review-async holds the Mac deployment (order 1) and the active GPU deployment (order 2);
* the review-local alias appears once, on the review-async Mac entry;
* the primary serving role and both direct aliases stay on the medium entries.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "llm-models.d"
MAC_MODEL = "mlx-community/Qwen3.8-27B-4bit"
GPU_PROFILE = "qwen38-96k-11-fp8-mtp"
GPU_MEDIUM = "gpu-medium-a-c11-96k"
GPU_REVIEW_ASYNC = "gpu-medium-a-c11-96k-review-async"


def _rows(name: str) -> list[dict]:
    doc = yaml.safe_load((REGISTRY / name).read_text(encoding="utf-8"))
    return [
        item
        for value in doc.values()
        if isinstance(value, list)
        for item in value
        if isinstance(item, dict)
    ]


def _rendered_rows() -> list[dict]:
    rows = _rows("10-large.yml") + _rows("60-gpu.yml")
    return [
        row
        for row in rows
        if row.get("tier") == "local-large"
        or (row.get("tier") == "gpu" and row.get("profile") == GPU_PROFILE)
    ]


def _members(route_group: str) -> list[tuple[str, int]]:
    return sorted(
        (row["client_model_id"], row["order"])
        for row in _rendered_rows()
        if row.get("route_group") == route_group
    )


def test_medium_members_and_orders_are_unchanged():
    assert _members("medium") == sorted(
        [(GPU_MEDIUM, 1), (MAC_MODEL, 2)]
    )


def test_review_async_is_mac_first_then_gpu():
    assert _members("review-async") == sorted(
        [(MAC_MODEL, 1), (GPU_REVIEW_ASYNC, 2)]
    )


def test_review_local_points_only_at_review_async():
    holders = [
        (row["client_model_id"], row["route_group"])
        for row in _rendered_rows()
        if "review-local" in (row.get("stable_aliases") or [])
    ]
    assert holders == [(MAC_MODEL, "review-async")]


def test_primary_role_and_direct_aliases_stay_on_medium():
    primary = [
        (row["client_model_id"], row["route_group"])
        for row in _rendered_rows()
        if row.get("serving_role") == "primary"
    ]
    assert primary == [(MAC_MODEL, "medium")]
    direct = {
        row["client_model_id"]: row.get("direct_route_aliases", [])
        for row in _rendered_rows()
        if row.get("direct_route_aliases")
    }
    assert direct == {
        GPU_MEDIUM: ["pro6000-direct"],
        MAC_MODEL: ["studio-direct"],
    }
