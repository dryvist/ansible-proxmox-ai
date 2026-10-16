"""The model campaign validates its benchmark endpoint and cache parameters before use."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = REPO_ROOT / "playbooks/llm-model-campaign.yml"
TARGET = REPO_ROOT / "playbooks/llm-model-campaign-target.yml"
ARTIFACTS = REPO_ROOT / "llm-models.d/65-gpu-artifacts.yml"


def _assert_conditions(node) -> list[str]:
    if isinstance(node, list):
        return [condition for item in node for condition in _assert_conditions(item)]
    if not isinstance(node, dict):
        return []
    conditions = []
    if "ansible.builtin.assert" in node:
        that = node["ansible.builtin.assert"]["that"]
        conditions.extend([that] if isinstance(that, str) else that)
    for value in node.values():
        conditions.extend(_assert_conditions(value))
    return conditions


def test_campaign_validates_the_endpoint_and_cache_parameters() -> None:
    plays = yaml.safe_load(CAMPAIGN.read_text(encoding="utf-8"))
    imports = [play.get("import_playbook", play.get("ansible.builtin.import_playbook")) for play in plays]
    assert TARGET.name in imports

    plays += yaml.safe_load(TARGET.read_text(encoding="utf-8"))
    conditions = "\n".join(_assert_conditions(plays))

    assert "benchmark_endpoint_root is defined" in conditions
    assert "benchmark_endpoint_root | string is match('^https://" in conditions
    assert "benchmark_cache_path is defined" in conditions
    assert "benchmark_cache_path is not search('(^|/)[.][.](/|$)')" in conditions


def test_large_batched_bench_is_an_artifact_capability() -> None:
    campaign = yaml.safe_load(CAMPAIGN.read_text(encoding="utf-8"))
    conditions = "\n".join(_assert_conditions(campaign))
    artifacts = yaml.safe_load(ARTIFACTS.read_text(encoding="utf-8"))["_llm_model_artifacts"]
    opted_in = [artifact["artifact_id"] for artifact in artifacts if artifact.get("supports_batched_bench")]

    assert "supports_batched_bench" in conditions
    assert opted_in == ["flash-next-iq3-s"]
