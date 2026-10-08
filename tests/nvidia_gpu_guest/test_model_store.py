"""Check pinned model-store contracts and cache safety across both engine roles."""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
from pathlib import Path
import re

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / "roles/nvidia_gpu_guest"
ENGINE_ROOTS = {
    "vllm": REPO_ROOT / "roles/vllm_serving",
    "llama_cpp": REPO_ROOT / "roles/llamacpp_serving",
}
ARTIFACT_FILES = (
    REPO_ROOT / "llm-models.d/65-gpu-artifacts.yml",
    REPO_ROOT / "llm-models.d/66-gpu-pro6000-artifacts-glm53flash.yml",
    REPO_ROOT / "llm-models.d/67-gpu-pro6000-artifacts-nvfp4-sweep.yml",
    REPO_ROOT / "llm-models.d/68-gpu-pro6000-stage0-artifacts.yml",
)
TARGET_FIXTURE = REPO_ROOT / "tests/llm_gpu_engine_roles/fixtures/pro6000-target"
LOCAL_VERIFIER = SHARED_ROOT / "files/verify-local-model-store.py"

_VERIFIER_SPEC = importlib.util.spec_from_file_location("verify_local_model_store", LOCAL_VERIFIER)
assert _VERIFIER_SPEC is not None and _VERIFIER_SPEC.loader is not None
_VERIFIER = importlib.util.module_from_spec(_VERIFIER_SPEC)
_VERIFIER_SPEC.loader.exec_module(_VERIFIER)


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
    assert len(model_store) == 25
    assert {artifact["model_store_profile"] for artifact in model_store} == {
        "small",
        "medium-a",
        "medium-b",
        "16gb",
        "max",
    }
    assert sum(artifact["model_store_size_bytes"] for artifact in model_store) == 461_354_853_641
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
        if task.get("name", "").startswith("Verify local files against pinned download sidecars")
    )
    notify = next(task for task in cache_tasks if task.get("name", "").startswith("Notify serving handlers"))

    assert any(task.get("name") == "Assert each serving profile resolves to exactly one artifact" for task in registry_tasks)
    assert any(
        task.get("name", "").startswith("Load the GLM-5.3-Flash artifact shard")
        and task["ansible.builtin.include_vars"]["file"] == "{{ nvidia_gpu_guest_glm_artifact_registry_file }}"
        for task in registry_tasks
    )
    assert any(
        task.get("name", "").startswith("Load the GLM-5.3-Flash artifact shard for cache sync")
        and task["ansible.builtin.include_vars"]["file"] == "{{ nvidia_gpu_guest_glm_artifact_registry_file }}"
        for task in cache_tasks
    )
    assert any(
        task.get("name", "").startswith("Load the NVFP4 sweep artifact shard")
        and task["ansible.builtin.include_vars"]["file"]
        == "{{ nvidia_gpu_guest_nvfp4_sweep_artifact_registry_file }}"
        for task in registry_tasks
    )
    assert any(
        task.get("name", "").startswith("Load the NVFP4 sweep artifact shard for cache sync")
        and task["ansible.builtin.include_vars"]["file"]
        == "{{ nvidia_gpu_guest_nvfp4_sweep_artifact_registry_file }}"
        for task in cache_tasks
    )
    assert any(
        task.get("name") == "Load Stage 0 benchmark artifacts"
        and task["ansible.builtin.include_vars"]["file"]
        == "{{ nvidia_gpu_guest_stage0_artifact_registry_file }}"
        for task in registry_tasks
    )
    assert any(
        task.get("name") == "Load Stage 0 benchmark artifacts for cache sync"
        and task["ansible.builtin.include_vars"]["file"]
        == "{{ nvidia_gpu_guest_stage0_artifact_registry_file }}"
        for task in cache_tasks
    )
    assert any(
        task.get("name", "").startswith("Combine the campaign model artifact shards")
        and "_llm_model_artifacts_glm53flash" in task["ansible.builtin.set_fact"]["nvidia_gpu_guest_model_artifacts"]
        and "_llm_model_artifacts_nvfp4_sweep"
        in task["ansible.builtin.set_fact"]["nvidia_gpu_guest_model_artifacts"]
        and "_llm_model_stage0_artifacts"
        in task["ansible.builtin.set_fact"]["nvidia_gpu_guest_model_artifacts"]
        for task in registry_tasks
    )
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
    assert "verify-local-model-store.py" in verify_local["ansible.builtin.script"]["cmd"]
    assert "--revision" in verify_local["ansible.builtin.script"]["cmd"]
    assert "--mode" not in verify_local["ansible.builtin.script"]["cmd"]
    assert "--repo-id" not in verify_local["ansible.builtin.script"]["cmd"]
    assert verify_local["changed_when"] is False
    assert cache_tasks.index(download) < cache_tasks.index(verify_local)
    assert cache_tasks.index(pull) < cache_tasks.index(verify_local)
    verify_origin = next(
        task
        for task in verify_tasks
        if task.get("name", "").startswith("Verify pinned model-store files")
    )
    assert "verify-local-model-store.py" in verify_origin["ansible.builtin.script"]["cmd"]
    assert "--mode" not in verify_origin["ansible.builtin.script"]["cmd"]
    assert verify_origin["changed_when"] is False
    assert any(
        task.get("name", "").startswith("Require a pinned registered artifact") for task in verify_tasks
    )
    for path in (
        SHARED_ROOT / "tasks/cache-sync.yml",
        REPO_ROOT / "roles/llm_gpu_serving/tasks/cache-sync.yml",
        SHARED_ROOT / "tasks/verify-model-store-origin-repo.yml",
        REPO_ROOT / "roles/llm_gpu_serving/tasks/verify-model-store-origin-repo.yml",
    ):
        assert "cache\n      - verify" not in path.read_text(encoding="utf-8")
    assert "list_repo_tree" not in LOCAL_VERIFIER.read_text(encoding="utf-8")
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


def _captured_target_files() -> dict[str, int]:
    listing = (TARGET_FIXTURE / "model-store-listing.txt").read_text(encoding="utf-8")
    lines = listing.splitlines()
    active_files = lines.index("active_files")
    return {
        filename: int(size_text.split()[0])
        for filename, size_text in (line.split("\t", maxsplit=1) for line in lines[active_files + 1 :])
    }


def _captured_target_repositories() -> set[str]:
    listing = (TARGET_FIXTURE / "model-store-listing.txt").read_text(encoding="utf-8")
    lines = listing.splitlines()
    active_files = lines.index("active_files")
    return {line.rstrip("/") for line in lines[1:active_files] if line.endswith("/")}


def test_real_target_fixture_provenance_is_recorded_and_private_endpoints_are_removed():
    readme = " ".join((TARGET_FIXTURE / "README.md").read_text(encoding="utf-8").split())
    assert "Captured on 2026-10-07 with read-only commands in the live GPU guest" in readme
    assert "nvidia-smi --query-gpu=name,driver_version,memory.total" in readme
    assert "relative model-store listing" in readme
    assert "systemctl cat llm-gpu-serving-medium-a.service" in readme
    for removed_field in (
        "guest and node identifiers",
        "absolute model-cache mount root",
        "listener address",
        "serials",
        "secret fields",
    ):
        assert removed_field in readme

    public_fixture = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TARGET_FIXTURE.iterdir()
        if path.is_file()
    )
    assert re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", public_fixture) is None
    dotted_tokens = set(re.findall(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b", public_fixture))
    allowed_dotted_tokens = set(_captured_target_files()) | {
        path.name for path in TARGET_FIXTURE.iterdir() if path.is_file()
    } | {
        "llm-gpu-serving-medium-a.service",
        "memory.total",
        "multi-user.target",
        "network-online.target",
    }
    assert dotted_tokens <= allowed_dotted_tokens
    assert "LISTEN_ADDRESS" in public_fixture
    assert "{{ llm_gpu_serving_model_cache_path }}" in public_fixture


def test_read_only_verifier_uses_captured_listing_for_repo_file_and_checksum_guard(tmp_path, capsys):
    target_files = _captured_target_files()
    repo_id = "nvidia/Qwen3.8-27B-NVFP4"
    assert repo_id in _captured_target_repositories()
    artifact = next(artifact for artifact in _model_store() if artifact["hf_repo"] == repo_id)
    relative_path = next(
        path
        for path in target_files
        if path == "model.safetensors.index.json"
        and any(fnmatch.fnmatchcase(path, pattern) for pattern in artifact["include_globs"])
    )
    captured_size = target_files[relative_path]
    assert captured_size == 214866

    root = tmp_path / "model-store" / repo_id
    file_path = root / relative_path
    file_path.parent.mkdir(parents=True)
    payload = b"{}\n" + b" " * (captured_size - 3)
    file_path.write_bytes(payload)
    etag = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
    metadata_path = root / ".cache/huggingface/download" / f"{relative_path}.metadata"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(f"{artifact['revision']}\n{etag}\n0\n", encoding="utf-8")
    arguments = [
        "--root",
        str(root),
        "--revision",
        artifact["revision"],
        "--include-globs",
        json.dumps(artifact["include_globs"]),
    ]

    assert _VERIFIER.main(arguments) == 0
    assert "Verified 1 pinned local model-store file(s)" in capsys.readouterr().out
    assert not (root / ".cache/huggingface/model-store-manifest.json").exists()

    metadata_path.write_text(f"{'0' * 40}\n{etag}\n0\n", encoding="utf-8")
    assert _VERIFIER.main(arguments) == 1
    assert "metadata revision differs" in capsys.readouterr().err
    metadata_path.write_text(f"{artifact['revision']}\n{etag}\n0\n", encoding="utf-8")

    no_match = [
        "--root",
        str(root),
        "--revision",
        artifact["revision"],
        "--include-globs",
        json.dumps([relative_path, "missing-*.json"]),
    ]
    assert _VERIFIER.main(no_match) == 1
    assert "no downloaded files: missing-*.json" in capsys.readouterr().err

    file_path.write_bytes(payload[:-1] + b"x")
    assert _VERIFIER.main(arguments) == 1
    assert "checksum differs" in capsys.readouterr().err


def test_cache_sync_roles_call_the_shared_read_only_sidecar_verifier():
    role_tasks = (
        REPO_ROOT / "roles/nvidia_gpu_guest/tasks/cache-sync.yml",
        REPO_ROOT / "roles/llm_gpu_serving/tasks/cache-sync.yml",
    )
    for path in role_tasks:
        tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
        verifier_task = next(
            task
            for task in tasks
            if task.get("name") == "Verify local files against pinned download sidecars"
        )
        command = verifier_task["ansible.builtin.script"]["cmd"]
        assert "verify-local-model-store.py" in command
        assert "hf cache verify" not in command
        assert "--mode" not in command
        assert verifier_task["changed_when"] is False
        download = next(task for task in tasks if task.get("name", "").startswith("Download only the registered"))
        assert tasks.index(download) < tasks.index(verifier_task)


def test_sidecar_verifier_checks_hf_lfs_sha256_metadata(tmp_path, capsys):
    root = tmp_path / "model-store" / "example/model"
    relative_path = "weights/model-00001-of-00001.safetensors"
    file_path = root / relative_path
    file_path.parent.mkdir(parents=True)
    payload = b"captured model shard checksum probe"
    file_path.write_bytes(payload)
    revision = "1234567890abcdef1234567890abcdef12345678"
    etag = hashlib.sha256(payload).hexdigest()
    metadata_path = root / ".cache/huggingface/download" / f"{relative_path}.metadata"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(f"{revision}\n{etag}\n0\n", encoding="utf-8")
    arguments = [
        "--root",
        str(root),
        "--revision",
        revision,
        "--include-globs",
        json.dumps([relative_path]),
    ]

    assert _VERIFIER.main(arguments) == 0
    assert "Verified 1 pinned local model-store file(s)" in capsys.readouterr().out
    assert not (root / ".cache/huggingface/model-store-manifest.json").exists()


@pytest.mark.parametrize("second_metadata", ["missing", "old-revision", "current"])
def test_every_selected_shard_requires_current_sidecar(tmp_path, capsys, second_metadata):
    root = tmp_path / "model-store"
    revision = "1" * 40
    payloads = {
        "model-00001-of-00003.safetensors": b"first shard",
        "model-00002-of-00003.safetensors": b"second shard",
        "model-00003-of-00003.safetensors": b"third shard",
    }
    metadata_root = root / ".cache/huggingface/download"
    metadata_root.mkdir(parents=True)
    for index, (name, payload) in enumerate(payloads.items()):
        (root / name).write_bytes(payload)
        if index == 1 and second_metadata == "missing":
            continue
        sidecar_revision = "2" * 40 if index == 1 and second_metadata == "old-revision" else revision
        etag = hashlib.sha256(payload).hexdigest()
        (metadata_root / f"{name}.metadata").write_text(f"{sidecar_revision}\n{etag}\n0\n")
    arguments = ["--root", str(root), "--revision", revision,
                 "--include-globs", json.dumps(["model-*.safetensors"])]
    if second_metadata == "current":
        assert _VERIFIER.main(arguments) == 0
        assert "Verified 3 pinned local model-store file(s)" in capsys.readouterr().out
        (root / "model-00003-of-00003.safetensors").write_bytes(b"corrupted shard")
        assert _VERIFIER.main(arguments) == 1
        assert "checksum differs" in capsys.readouterr().err
    else:
        assert _VERIFIER.main(arguments) == 1
        expected = "metadata is missing" if second_metadata == "missing" else "metadata revision differs"
        assert expected in capsys.readouterr().err
