"""Contracts for the MLX Mac campaign lane."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
TARGET = PLAYBOOKS / "llm-model-campaign-target.yml"
DIMENSIONS = PLAYBOOKS / "templates/llm-model-campaign-dimensions.json.j2"
MLX_ARTIFACTS = REPO_ROOT / "llm-models.d/69-gpu-artifacts-mlx.yml"


def test_mlx_recipes_are_admitted_and_the_campaign_revision_is_pinned():
    plays = yaml.safe_load((PLAYBOOKS / "llm-model-campaign.yml").read_text(encoding="utf-8"))
    conditions = plays[0]["tasks"][0]["ansible.builtin.assert"]["that"]
    pattern = re.fullmatch(r"config_name is match\('(.+)'\)", next(item for item in conditions if item.startswith("config_name is match(")))
    assert pattern is not None

    for name in ("mlx/vlm-native", "mlx/vlm-native-mtp", "mlx/vllm-mlx"):
        assert re.match(pattern.group(1), name)
    assert re.fullmatch(r"[0-9a-f]{40}", plays[0]["vars"]["_llm_campaign_benchmarks_ref"])


def test_mlx_engines_allow_zero_power_cap_only_on_darwin():
    plays = yaml.safe_load(TARGET.read_text(encoding="utf-8"))
    guard = next(
        task
        for task in plays[0]["pre_tasks"]
        if task.get("name") == "Verify the declared power cap against the target GPU or platform"
    )
    assertion = next(task for task in guard["block"] if task.get("name") == "Assert the survey cap matches target telemetry")
    evaluate = jinja2.Environment(undefined=jinja2.StrictUndefined).compile_expression(
        assertion["ansible.builtin.assert"]["that"][0]
    )

    for engine in ("mlx_lm", "mlx_vlm_native", "vllm_mlx"):
        assert evaluate(
            _llm_campaign_power_limit={"rc": 1, "stdout_lines": []},
            _llm_campaign_system_name={"stdout": "Darwin\n"},
            engine=engine,
            power_cap_w=0,
        )


def test_mlx_artifact_shard_uses_revision_pins_and_resolves_mtp_pairs():
    registry = yaml.safe_load(MLX_ARTIFACTS.read_text(encoding="utf-8"))
    artifacts = registry["_llm_model_artifacts_mlx"]
    by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}

    assert artifacts
    assert all(re.fullmatch(r"[0-9a-f]{40}", artifact["revision"]) for artifact in artifacts)
    for artifact in artifacts:
        mtp_artifact_id = artifact.get("mtp_artifact_id")
        if mtp_artifact_id:
            assert mtp_artifact_id in by_id
            assert artifact["context_window_tokens"] == 262144


def test_mlx_serving_engines_render_as_metal():
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters["regex_search"] = lambda *_args: None
    environment.filters["to_nice_json"] = json.dumps
    template = environment.from_string(DIMENSIONS.read_text(encoding="utf-8"))
    context = {
        "engine": "mlx_vlm_native",
        "config_name": "mlx/vlm-native",
        "ansible_facts": {
            "distribution": "ExampleOS",
            "distribution_version": "26",
            "system": "Darwin",
            "kernel": "25.0.0",
            "processor": ["Example Silicon"],
            "memtotal_mb": 131072,
        },
        "_llm_campaign_model": {"artifact_id": "sample", "hf_repo": "org/model"},
        "_llm_campaign_run": {"output_tokens": 128, "repetitions": 2},
        "_llm_campaign_concurrency_value": 2,
        "_llm_campaign_started_utc": {"stdout": "2026-10-16T00:00:00Z"},
        "_llm_campaign_workdir": "/tmp/campaign",
        "_llm_campaign_cell_slug": "sample-ctx262016-c2-r1",
        "hostvars": {"localhost": {"_llm_campaign_config_ref": "f" * 40}},
    }

    for engine in ("mlx_vlm_native", "vllm_mlx"):
        context["engine"] = engine
        software = json.loads(template.render(**context))["campaign_dimensions"]["software"]
        assert software["engine"] == "MLX"
        assert software["backend"] == "Metal"
