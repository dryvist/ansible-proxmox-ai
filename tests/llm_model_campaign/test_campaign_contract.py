"""The model campaign validates its benchmark endpoint and cache parameters before use."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = REPO_ROOT / "playbooks/llm-model-campaign.yml"
TARGET = REPO_ROOT / "playbooks/llm-model-campaign-target.yml"
ENDPOINT_POLICY = REPO_ROOT / "playbooks/tasks/llm-model-campaign-endpoint-policy.yml"
ARTIFACTS = tuple(sorted((REPO_ROOT / "llm-models.d").glob("*artifacts*.yml")))


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
    plays += yaml.safe_load(ENDPOINT_POLICY.read_text(encoding="utf-8"))
    conditions = "\n".join(_assert_conditions(plays))

    assert "benchmark_endpoint_root is defined" in conditions
    assert "benchmark_endpoint_root | string" in conditions
    assert "^https://" in conditions
    assert "llm_model_campaign_allow_trial_http | default(false) | bool" in conditions
    assert "== llm_model_trial_endpoint_url | regex_replace('/v1$', '')" in conditions
    assert "benchmark_cache_path is defined" in conditions
    assert "benchmark_cache_path is not search('(^|/)[.][.](/|$)')" in conditions
    assert "artifact_id is defined" in conditions


def test_campaign_selects_one_exact_registry_artifact() -> None:
    plays = yaml.safe_load(CAMPAIGN.read_text(encoding="utf-8"))
    tasks = plays[0]["tasks"]
    resolve = next(task for task in tasks if task.get("name", "").startswith("Resolve the selected artifact id"))
    selection = resolve["ansible.builtin.set_fact"]["_llm_campaign_selected_artifacts"]

    assert "selectattr('artifact_id', 'equalto', artifact_id)" in selection
    assert "_llm_campaign_selected_artifacts | length == 1" in "\n".join(_assert_conditions(plays))


def test_campaign_loads_artifacts_through_the_shared_loader() -> None:
    tasks = yaml.safe_load(CAMPAIGN.read_text(encoding="utf-8"))[0]["tasks"]
    assert any(
        task.get("name") == "Load the single-source model artifact registry"
        and task["ansible.builtin.include_role"] == {
            "name": "llm_gpu_serving",
            "tasks_from": "load-artifacts.yml",
        }
        for task in tasks
    )


def test_large_batched_bench_is_an_artifact_capability() -> None:
    campaign = yaml.safe_load(CAMPAIGN.read_text(encoding="utf-8"))
    conditions = "\n".join(_assert_conditions(campaign))
    artifacts = [
        artifact
        for path in ARTIFACTS
        for key, entries in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_model_artifacts") or key.endswith("_artifacts")
        for artifact in entries
    ]
    opted_in = [artifact["artifact_id"] for artifact in artifacts if artifact.get("supports_batched_bench")]

    assert "supports_batched_bench" in conditions
    assert opted_in == ["flash-next-iq3-s"]
