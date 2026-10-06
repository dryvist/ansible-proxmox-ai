"""Check the registry-driven model store: pinned artifacts, writer group, and the download-then-pull tasks."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/llm_gpu_serving"
ARTIFACT_FILE = REPO_ROOT / "llm-models.d/65-gpu-pro6000-artifacts.yml"


def _model_store() -> list[dict]:
    artifacts = yaml.safe_load(ARTIFACT_FILE.read_text(encoding="utf-8"))["_llm_model_artifacts"]
    return [artifact for artifact in artifacts if artifact.get("model_store") is True]


def test_model_store_registry_pins_every_artifact_and_covers_each_profile():
    model_store = _model_store()
    assert len(model_store) == 22
    assert {artifact["model_store_profile"] for artifact in model_store} == {
        "small",
        "medium-a",
        "medium-b",
        "max",
    }
    assert sum(artifact["model_store_size_bytes"] for artifact in model_store) == 334_707_157_291
    assert all(len(artifact["revision"]) == 40 for artifact in model_store)
    assert all(set(artifact["revision"]) <= set("0123456789abcdef") for artifact in model_store)
    assert {artifact["artifact_id"] for artifact in model_store if artifact.get("model_size")} == {
        "qwen35-9b-nvfp4",
        "qwen38-27b-nvfp4",
        "qwen38-27b-ud-q4-k-m",
        "flash-next-iq3-s",
    }
    repositories = {artifact["hf_repo"] for artifact in model_store}
    for repository in repositories:
        entries = [artifact for artifact in model_store if artifact["hf_repo"] == repository]
        assert len({artifact["revision"] for artifact in entries}) == 1
        selectors = [selector for artifact in entries for selector in artifact["include_globs"]]
        assert len(selectors) == len(set(selectors))
        if len(entries) > 1:
            assert all(not any(character in selector for character in "*?[") for selector in selectors)

    assert any(artifact["format"] == "GGUF" and "llama_cpp" in artifact["engines"] for artifact in model_store)
    assert any(artifact["format"] == "safetensors" and "vllm" in artifact["engines"] for artifact in model_store)
    assert any(artifact["format"] == "MLX" and "mlx_lm" in artifact["engines"] for artifact in model_store)


def test_model_store_writer_group_comes_from_declared_gpu_mount_access():
    inventory_tasks = yaml.safe_load(
        (REPO_ROOT / "inventory/load_tofu/add_lxc_hosts.yml").read_text(encoding="utf-8")
    )
    add_host = next(
        task["ansible.builtin.add_host"]
        for task in inventory_tasks
        if task.get("name", "").startswith("Add AI LXC containers to inventory")
    )
    assert "llm_model_store_writer_group" in add_host["groups"]
    assert "models_origin_mount_path" in add_host["groups"]
    assert "models_origin_mount_read_only" in add_host["groups"]
    assert "item.value.models_origin_mount_path" in add_host["container_models_origin_mount_path"]


def test_model_store_downloads_pinned_artifacts_then_pulls_from_origin():
    main_tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    cache_tasks = yaml.safe_load((ROLE_ROOT / "tasks/cache-sync.yml").read_text(encoding="utf-8"))
    verify_tasks = yaml.safe_load(
        (ROLE_ROOT / "tasks/verify-model-store-origin-repo.yml").read_text(encoding="utf-8")
    )
    seed_playbook = yaml.safe_load((REPO_ROOT / "playbooks/llm-model-store-seed.yml").read_text(encoding="utf-8"))[0]
    include = next(task for task in main_tasks if task.get("name", "").startswith("Cache the active profile"))
    preview = next(
        task
        for task in cache_tasks
        if task.get("name", "").startswith("Preview each registered artifact")
    )
    download = next(
        task
        for task in cache_tasks
        if task.get("name", "").startswith("Download only the registered pinned artifact")
    )
    pull = next(
        task
        for task in cache_tasks
        if task.get("name", "").startswith("Copy the registered artifact from the shared origin")
    )
    verify_local = next(
        task
        for task in cache_tasks
        if task.get("name", "").startswith("Verify the local artifact")
    )
    notify = next(task for task in cache_tasks if task.get("name", "").startswith("Notify serving handlers"))
    assert include["ansible.builtin.include_tasks"] == "cache-sync.yml"
    assert "llm_gpu_serving_active_artifact.required_artifact_ids" in include["loop"]
    assert include["loop_control"]["loop_var"] == "llm_gpu_serving_cache_sync_artifact_id"
    assert include["vars"]["llm_gpu_serving_cache_sync_notify_service"] is True
    assert "llm_gpu_serving_cache_sync_artifact.hf_repo" in preview["ansible.builtin.command"]["argv"]
    assert "llm_gpu_serving_cache_sync_artifact.revision" in preview["ansible.builtin.command"]["argv"]
    assert "llm_gpu_serving_cache_sync_artifact.include_globs" in preview["loop"]
    assert "--dry-run" in preview["ansible.builtin.command"]["argv"]
    assert preview["changed_when"] is False
    assert "llm_gpu_serving_cache_sync_artifact.hf_repo" in download["ansible.builtin.command"]["argv"]
    assert "llm_gpu_serving_cache_sync_artifact.revision" in download["ansible.builtin.command"]["argv"]
    assert "llm_gpu_serving_cache_sync_download_directory" in download["ansible.builtin.command"]["argv"]
    assert "llm_gpu_serving_cache_sync_download_directory" in preview["ansible.builtin.command"]["argv"]
    validate = next(task for task in main_tasks if task.get("name", "").startswith("Validate the active GPU serving profile"))
    assert "llm_gpu_serving_model_cache_mount_path | length > 0" in validate["ansible.builtin.assert"]["that"]
    assert "llm_gpu_serving_model_origin_mount_path" not in str(validate)
    assert "llm_gpu_serving_cache_sync_previews.results[ansible_loop.index0]" in download["changed_when"]
    assert download["become_user"] == "{{ llm_gpu_serving_user }}"
    assert download["loop"] == "{{ llm_gpu_serving_cache_sync_artifact.include_globs }}"
    assert "--local-dir" in download["ansible.builtin.command"]["argv"]
    assert "not llm_gpu_serving_model_origin_mount_read_only" in str(
        next(task for task in cache_tasks if task.get("name", "").startswith("Assert the requested artifact"))["ansible.builtin.assert"]["that"]
    )
    assert "nvidia-smi" in str(
        next(task for task in cache_tasks if task.get("name", "").startswith("Read compute applications"))["ansible.builtin.command"]["argv"]
    )
    assert "stdout | trim | length == 0" in str(
        next(task for task in cache_tasks if task.get("name", "").startswith("Require an idle GPU"))["ansible.builtin.assert"]["that"]
    )
    assert pull["ansible.builtin.copy"]["remote_src"] is True
    assert "llm_gpu_serving_cache_sync_origin_directory" in pull["ansible.builtin.copy"]["src"]
    assert "llm_gpu_serving_cache_sync_local_directory" in pull["ansible.builtin.copy"]["dest"]
    assert verify_local["ansible.builtin.command"]["argv"]
    assert any(task.get("name", "").startswith("Verify checksums") for task in verify_tasks)
    assert seed_playbook["hosts"] == "llm_model_store_writer_group"
    assert "llm_model_store_seed_artifacts" in seed_playbook["tasks"][0]["loop"]
    assert "notify" not in download
    assert notify["when"] == [
        "not ansible_check_mode",
        "(llm_gpu_serving_cache_sync_mode | default('pull')) == 'pull'",
        "llm_gpu_serving_cache_sync_notify_service | default(false) | bool",
        "llm_gpu_serving_cache_sync_copy.changed | default(false)",
    ]
    assert notify["notify"] == [
        "Stop every other GPU serving profile before switching",
        "Start the active GPU serving profile",
    ]
    assert "rsync" not in (ROLE_ROOT / "tasks/cache-sync.yml").read_text(encoding="utf-8")
