"""Replay local model-store checksums and sanitized target-file provenance."""

from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
import re

import pytest
import yaml

from test_model_store import LOCAL_VERIFIER, REPO_ROOT, _model_store

TARGET_FIXTURE = REPO_ROOT / "tests/llm_gpu_engine_roles/fixtures/pro6000-target"
_VERIFIER_SPEC = importlib.util.spec_from_file_location("verify_local_model_store", LOCAL_VERIFIER)
assert _VERIFIER_SPEC is not None and _VERIFIER_SPEC.loader is not None
_VERIFIER = importlib.util.module_from_spec(_VERIFIER_SPEC)
_VERIFIER_SPEC.loader.exec_module(_VERIFIER)


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
    unit = (TARGET_FIXTURE / "rendered-medium-a.service").read_text(encoding="utf-8")
    served_model = re.search(r"--served-model-name\s+(\S+)", unit)
    assert served_model is not None
    repo_id = served_model.group(1)
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
