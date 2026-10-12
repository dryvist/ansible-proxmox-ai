from __future__ import annotations

import importlib
import runpy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
SELECTOR = ROOT / ".github/scripts/select-contract-scope.py"
sys.path.insert(0, str(SELECTOR.parent))
router_shards_module = importlib.import_module("router_shards")
MAX_ROUTER_SHARDS = router_shards_module.MAX_ROUTER_SHARDS
ROUTER_WEIGHTS = router_shards_module.ROUTER_WEIGHTS
router_shard_target = router_shards_module.router_shard_target
router_shards = router_shards_module.router_shards
router_weight = router_shards_module.router_weight

SELECTOR_MODULE = runpy.run_path(str(SELECTOR))


def router_paths(shards: list[dict[str, str]]) -> list[str]:
    return [path for shard in shards for path in shard["playbooks"].split()]


def test_full_matrix_is_weight_balanced_and_deterministic() -> None:
    matrix = SELECTOR_MODULE["llm_router_matrix"]()
    shards = router_shards(matrix)
    paths = router_paths(shards)
    expected = [path for entry in matrix for path in entry]
    shard_weights = [sum(router_weight(path) for path in shard["playbooks"].split())
                     for shard in shards]

    assert len(shards) <= MAX_ROUTER_SHARDS
    assert len(paths) == len(set(paths)) == len(expected)
    assert sorted(paths) == sorted(expected)
    assert max(shard_weights) <= 20
    assert router_shards(matrix) == shards


def test_weight_keys_are_existing_playbook_files() -> None:
    assert all((ROOT / path).is_file() for path in ROUTER_WEIGHTS)


def test_unlisted_playbook_has_default_weight_one() -> None:
    known = "tests/llm_router/test_review_key_scopes.yml"
    unlisted = "tests/llm_router/test_synthetic_unlisted.yml"
    matrix = [[known, unlisted]]

    assert unlisted not in ROUTER_WEIGHTS
    assert router_weight(unlisted) == 1
    assert router_shard_target(matrix) == (router_weight(known) + 2 - 1) // 2


def test_subset_selection_keeps_unique_shards_and_exact_playbooks() -> None:
    selected_paths = [
        "tests/llm_router/test_review_key_scopes.yml",
        "tests/llm_router/test_service_restart_policy.yml",
    ]
    shards = SELECTOR_MODULE["select"](selected_paths)["llm_router_playbooks"]
    paths = router_paths(shards)

    assert len({shard["name"] for shard in shards}) == len(shards)
    assert len(paths) == len(set(paths)) == len(selected_paths)
    assert sorted(paths) == sorted(selected_paths)
