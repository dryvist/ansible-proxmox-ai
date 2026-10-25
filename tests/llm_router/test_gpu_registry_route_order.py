"""Check router order reuse across GPU profile registry shards."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_FILES = (
    REPO_ROOT / "llm-models.d/60-gpu.yml",
    REPO_ROOT / "llm-models.d/61-gpu-matrix.yml",
)


def test_route_order_reuse_is_limited_to_distinct_gpu_profiles() -> None:
    orders: dict[tuple[str, int], list[dict]] = {}
    for path in REGISTRY_FILES:
        registry = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = next(value for key, value in registry.items() if key.startswith("_llm_registry_gpu"))
        for entry in entries:
            if route_group := entry.get("route_group"):
                orders.setdefault((route_group, entry["order"]), []).append(entry)
    for (route_group, order), entries in orders.items():
        profiles = [entry.get("profile") for entry in entries]
        if len(entries) > 1:
            assert all(profiles) and len(set(profiles)) == len(profiles), (
                f"{route_group} order {order} must use distinct profiles"
            )
