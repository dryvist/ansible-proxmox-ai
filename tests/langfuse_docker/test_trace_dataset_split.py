"""Verify the stable train/holdout assignment used by trace reconciliation."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = REPO_ROOT / "roles/langfuse_docker/defaults/main/00-main.yml"
DATASETS_RECONCILE = REPO_ROOT / "roles/langfuse_docker/tasks/reconcile-stage0-trace-datasets.yml"
RECONCILE = REPO_ROOT / "roles/langfuse_docker/tasks/reconcile-stage0-trace-page.yml"


@pytest.fixture
def trace_ids():
    return [f"agent-trace-{index:05d}" for index in range(10_000)]


def _split(trace_id: str, holdout_fraction: float) -> str:
    bucket = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) % 10_000
    cutoff = int(holdout_fraction * 10_000)
    return "holdout" if bucket < cutoff else "train"


def _assign(trace_ids: list[str], holdout_fraction: float) -> dict[str, set[str]]:
    assigned = {"train": set(), "holdout": set()}
    for trace_id in trace_ids:
        assigned[_split(trace_id, holdout_fraction)].add(trace_id)
    return assigned


def test_trace_split_is_deterministic_and_disjoint(trace_ids):
    defaults = yaml.safe_load(DEFAULTS.read_text())
    fraction = defaults["langfuse_docker_stage0_trace_holdout_fraction"]
    first = _assign(trace_ids, fraction)
    second = _assign(trace_ids, fraction)
    datasets_reconcile = yaml.safe_load(DATASETS_RECONCILE.read_text())
    declared = yaml.safe_load(
        (DEFAULTS.parent / "25-trace-datasets.yml").read_text()
    )["langfuse_docker_stage0_trace_datasets"]
    native_defaults = yaml.safe_load(
        (DEFAULTS.parent / "30-native-evaluation.yml").read_text()
    )
    assert native_defaults["langfuse_docker_datasets"] == (
        "{{ langfuse_docker_stage0_trace_datasets }}"
    )
    assert not any("langfuse_docker_stage0_trace_datasets" in task.get(
        "ansible.builtin.set_fact", {}
    ) for task in datasets_reconcile)
    read = next(task for task in datasets_reconcile
                if task.get("name") == "Read the Stage 0 trace split datasets")
    assert read["loop"] == "{{ langfuse_docker_stage0_trace_datasets }}"
    source = re.sub(r"\s+", " ", RECONCILE.read_text())

    assert [dataset["metadata"]["split"] for dataset in declared] == ["train", "holdout"]
    assert [dataset["name"] for dataset in declared] == [
        "{{ langfuse_docker_stage0_dataset_name }}-train",
        "{{ langfuse_docker_stage0_dataset_name }}-holdout",
    ]
    assert "((item.traceId | hash('sha256'))[:8] | int(base=16)) % 10000" in source
    assert "langfuse_docker_stage0_trace_holdout_fraction" in source
    assert (
        "langfuse_docker_stage0_trace_datasets "
        "| selectattr('metadata.split', 'equalto', langfuse_docker_stage0_trace_split) "
        "| first).name"
    ) in source
    assert "sourceTraceId: \"{{ item.traceId }}\"" in source
    assert first == second
    assert first["train"].isdisjoint(first["holdout"])
    assert first["train"] | first["holdout"] == set(trace_ids)
    train_item_ids = {f"agent-trace-train-{trace_id}" for trace_id in first["train"]}
    holdout_item_ids = {f"agent-trace-holdout-{trace_id}" for trace_id in first["holdout"]}
    assert train_item_ids.isdisjoint(holdout_item_ids)


def test_holdout_fraction_default_is_honored_for_fixture_trace_ids(trace_ids):
    defaults = yaml.safe_load(DEFAULTS.read_text())
    fraction = defaults["langfuse_docker_stage0_trace_holdout_fraction"]
    assigned = _assign(trace_ids, fraction)
    holdout_fraction = len(assigned["holdout"]) / len(trace_ids)

    assert defaults["langfuse_docker_stage0_trace_reconcile_enabled"] is False
    assert fraction == 0.2
    assert abs(holdout_fraction - fraction) <= 0.02
