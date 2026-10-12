"""Keep benchmark-only model artifacts registered without entering serving profiles."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE0_FILE = REPO_ROOT / "llm-models.d/68-gpu-stage0-artifacts.yml"
ENGINE_ROLES = ("nvidia_gpu_guest", "llm_gpu_serving")
SUPPORTED_SERVING_ENGINES = {"vllm", "llama_cpp", "mlx_lm"}


def _engine_guard_accepts(artifact: dict) -> bool:
    return artifact["use"] == "benchmark-only" or set(artifact["engines"]) <= (
        SUPPORTED_SERVING_ENGINES
    )


def test_stage0_artifacts_are_pinned_and_separate_from_campaign_data():
    artifacts = yaml.safe_load(STAGE0_FILE.read_text(encoding="utf-8"))[
        "_llm_model_stage0_artifacts"
    ]
    by_id = {artifact["artifact_id"]: artifact for artifact in artifacts}

    assert set(by_id) == {
        "embeddinggemma-2-stage0",
        "opendecider-nano-stage0",
        "laya-typed-decisions-stage0",
    }
    for artifact in artifacts:
        assert len(artifact["revision"]) == 40
        assert artifact["use"] == "benchmark-only"
        assert artifact["model_card_url"].endswith(f"/{artifact['revision']}/README.md")
        assert artifact["model_card_read_date"] == "2026-10-07"
        assert artifact["parameter_count"] > 0
        assert artifact["engines"] in [["vllm"], ["opendecider"], ["laya"]]
        assert artifact["model_task"]
        assert artifact["model_task_source"] in {"model_card", "inferred"}
        assert "model_size" not in artifact
        assert "model_store" not in artifact

    assert by_id["opendecider-nano-stage0"]["engines"] == ["opendecider"]
    assert by_id["laya-typed-decisions-stage0"]["engines"] == ["laya"]


def test_engine_and_cache_sync_loaders_include_stage0_artifacts():
    for role in ENGINE_ROLES:
        role_root = REPO_ROOT / "roles" / role
        load_tasks = yaml.safe_load((role_root / "tasks/load-registry.yml").read_text())
        cache_tasks = yaml.safe_load((role_root / "tasks/cache-sync.yml").read_text())

        assert any("stage0_artifact_registry_file" in str(task) for task in load_tasks)
        assert any("stage0_artifact_registry_file" in str(task) for task in cache_tasks)
        for tasks in (load_tasks, cache_tasks):
            combine = next(
                str(task)
                for task in tasks
                if "_llm_model_stage0_artifacts" in str(task)
                and f"{role}_model_artifacts" in str(task)
            )
            assert "_llm_model_artifacts_glm53flash" in combine
            assert "_llm_model_artifacts_nvfp4_sweep" in combine
        assert any(f"{role}_model_artifacts" in str(task) for task in load_tasks)
        assert any(f"{role}_model_artifacts" in str(task) for task in cache_tasks)


def test_loader_checks_model_task_provenance_for_all_artifacts():
    for role in ENGINE_ROLES:
        tasks = yaml.safe_load(
            (REPO_ROOT / "roles" / role / "tasks/load-registry.yml").read_text()
        )
        validation = next(
            task
            for task in tasks
            if task.get("name") == "Validate model artifact metadata"
        )
        conditions = validation["ansible.builtin.assert"]["that"]

        assert (
            "item.model_task is not defined or item.model_task | length > 0"
            in conditions
        )
        assert (
            "item.model_task_source is not defined or item.model_task_source in ['model_card', 'inferred']"
            in conditions
        )
        assert (
            "(item.model_task is defined) == (item.model_task_source is defined)"
            in conditions
        )
        assert (
            "item.model_size is not defined or item.model_task is defined" in conditions
        )
        assert validation["loop"] == f"{{{{ {role}_model_artifacts }}}}"


def test_benchmark_only_artifacts_can_name_external_evaluation_engines():
    stage0 = yaml.safe_load(STAGE0_FILE.read_text(encoding="utf-8"))[
        "_llm_model_stage0_artifacts"
    ]

    for role in ENGINE_ROLES:
        tasks = yaml.safe_load(
            (REPO_ROOT / "roles" / role / "tasks/load-registry.yml").read_text(
                encoding="utf-8"
            )
        )
        validation = next(
            task
            for task in tasks
            if task.get("name") == "Validate model artifact metadata"
        )
        conditions = validation["ansible.builtin.assert"]["that"]
        engine_guard = next(
            condition
            for condition in conditions
            if "item.engines | difference" in condition
        )

        assert "item.use == 'benchmark-only'" in engine_guard
        assert all(_engine_guard_accepts(item) for item in stage0)
        assert _engine_guard_accepts({"use": "benchmark-only", "engines": ["laya"]})
        assert not _engine_guard_accepts({"use": "serving", "engines": ["laya"]})
        assert _engine_guard_accepts({"use": "serving", "engines": ["vllm"]})
