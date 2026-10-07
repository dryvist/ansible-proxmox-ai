"""Check pinned model-store contracts and cache safety across both engine roles."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
ENGINE_ROOTS = {
    "vllm": REPO_ROOT / "roles/vllm_serving",
    "llama_cpp": REPO_ROOT / "roles/llamacpp_serving",
}
ARTIFACT_FILES = (
    REPO_ROOT / "llm-models.d/65-gpu-pro6000-artifacts.yml",
    REPO_ROOT / "llm-models.d/66-gpu-pro6000-artifacts-glm53flash.yml",
)


def _model_store() -> list[dict]:
    artifacts = [
        artifact
        for path in ARTIFACT_FILES
        for key, entries in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_model_artifacts")
        for artifact in entries
    ]
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
        (REPO_ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml").read_text(encoding="utf-8")
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
    registry_tasks = yaml.safe_load((SHARED_ROOT / "tasks/load-registry.yml").read_text(encoding="utf-8"))
    cache_tasks = yaml.safe_load((SHARED_ROOT / "tasks/cache-sync.yml").read_text(encoding="utf-8"))
    verify_tasks = yaml.safe_load(
        (SHARED_ROOT / "tasks/verify-model-store-origin-repo.yml").read_text(encoding="utf-8")
    )
    seed_playbook = yaml.safe_load((REPO_ROOT / "playbooks/llm-model-store-seed.yml").read_text(encoding="utf-8"))[0]
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

    assert any(task.get("name") == "Assert each serving profile resolves to exactly one artifact" for task in registry_tasks)
    for engine, role_root in ENGINE_ROOTS.items():
        main_tasks = yaml.safe_load((role_root / "tasks/main.yml").read_text(encoding="utf-8"))
        activation_tasks = yaml.safe_load((role_root / "tasks/activate.yml").read_text(encoding="utf-8"))
        validation_tasks = yaml.safe_load((role_root / "tasks/validate-profiles.yml").read_text(encoding="utf-8"))
        prefix = "vllm_serving" if engine == "vllm" else "llamacpp_serving"
        include = next(
            task
            for task in activation_tasks
            if task.get("name", "").startswith("Cache the active profile")
        )
        validate = next(
            task
            for task in validation_tasks
            if task.get("name", "").startswith("Validate the active GPU serving profile")
        )

        assert any(
            task.get("ansible.builtin.include_role", {}).get("tasks_from") == "load-registry.yml"
            for task in main_tasks
        )
        assert include["ansible.builtin.include_role"] == {
            "name": "nvidia_gpu_guest",
            "tasks_from": "cache-sync.yml",
        }
        assert include["loop_control"]["loop_var"] == f"{prefix}_cache_sync_artifact_id"
        assert f"{prefix}_active_artifact.required_artifact_ids" in include["loop"]
        assert f"{prefix}_active_profile" in yaml.safe_dump(validate)
        assert "nvidia_gpu_guest_model_cache_mount_path | length > 0" in validate["ansible.builtin.assert"]["that"]
        assert "nvidia_gpu_guest_model_origin_mount_path" not in str(validate)

    assert "nvidia_gpu_guest_cache_sync_artifact.required_artifact_ids" not in str(registry_tasks)
    assert "nvidia_gpu_guest_cache_sync_artifact.hf_repo" in preview["ansible.builtin.command"]["argv"]
    assert "nvidia_gpu_guest_cache_sync_artifact.revision" in preview["ansible.builtin.command"]["argv"]
    assert "nvidia_gpu_guest_cache_sync_artifact.include_globs" in preview["loop"]
    assert "--dry-run" in preview["ansible.builtin.command"]["argv"]
    assert preview["changed_when"] is False
    assert "nvidia_gpu_guest_cache_sync_artifact.hf_repo" in download["ansible.builtin.command"]["argv"]
    assert "nvidia_gpu_guest_cache_sync_artifact.revision" in download["ansible.builtin.command"]["argv"]
    assert "nvidia_gpu_guest_cache_sync_download_directory" in download["ansible.builtin.command"]["argv"]
    assert "nvidia_gpu_guest_cache_sync_download_directory" in preview["ansible.builtin.command"]["argv"]
    assert "nvidia_gpu_guest_cache_sync_previews.results[ansible_loop.index0]" in download["changed_when"]
    assert download["become_user"] == "{{ nvidia_gpu_guest_user }}"
    assert download["loop"] == "{{ nvidia_gpu_guest_cache_sync_artifact.include_globs }}"
    assert "--local-dir" in download["ansible.builtin.command"]["argv"]
    assert "not nvidia_gpu_guest_model_origin_mount_read_only" in str(
        next(task for task in cache_tasks if task.get("name", "").startswith("Assert the requested artifact"))["ansible.builtin.assert"]["that"]
    )
    assert "nvidia-smi" in str(
        next(task for task in cache_tasks if task.get("name", "").startswith("Read compute applications"))["ansible.builtin.command"]["argv"]
    )
    assert "stdout | trim | length == 0" in str(
        next(task for task in cache_tasks if task.get("name", "").startswith("Require an idle GPU"))["ansible.builtin.assert"]["that"]
    )
    assert pull["ansible.builtin.copy"]["remote_src"] is True
    assert "nvidia_gpu_guest_cache_sync_origin_directory" in pull["ansible.builtin.copy"]["src"]
    assert "nvidia_gpu_guest_cache_sync_local_directory" in pull["ansible.builtin.copy"]["dest"]
    assert verify_local["ansible.builtin.command"]["argv"]
    assert any(task.get("name", "").startswith("Verify checksums") for task in verify_tasks)
    assert seed_playbook["hosts"] == "llm_model_store_writer_group"
    assert "llm_model_store_seed_artifacts" in seed_playbook["tasks"][0]["loop"]
    assert "notify" not in download
    assert notify["when"] == [
        "not ansible_check_mode",
        "(nvidia_gpu_guest_cache_sync_mode | default('pull')) == 'pull'",
        "nvidia_gpu_guest_cache_sync_notify_service | default(false) | bool",
        "nvidia_gpu_guest_cache_sync_copy.changed | default(false)",
    ]
    assert notify["notify"] == [
        "Stop every other GPU serving profile before switching",
        "Start the active GPU serving profile",
    ]
    assert "rsync" not in (SHARED_ROOT / "tasks/cache-sync.yml").read_text(encoding="utf-8")


def test_profile_switch_clears_only_its_own_gpu_work_before_downloading():
    cache_tasks = yaml.safe_load((SHARED_ROOT / "tasks/cache-sync.yml").read_text(encoding="utf-8"))
    names = [task.get("name", "") for task in cache_tasks]
    stop_index = names.index("Stop this role's serving units before a profile switch downloads an artifact")
    guard_index = names.index("Require an idle GPU before downloading a registered artifact")
    stop, guard = cache_tasks[stop_index], cache_tasks[guard_index]
    assert stop_index < guard_index
    assert "nvidia_gpu_guest_cache_sync_notify_service | default(false) | bool" in stop["when"]
    assert stop["notify"] == "Start the active GPU serving profile"
    assert "{{ nvidia_gpu_guest_profiles | dict2items }}" == stop["loop"]
    assert "nvidia_gpu_guest_cache_sync_notify_service" in (
        SHARED_ROOT / "defaults/main/10-engine-context.yml"
    ).read_text(encoding="utf-8")
