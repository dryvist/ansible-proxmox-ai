"""Qdrant pre-upgrade snapshot safety contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE = REPO_ROOT / "roles" / "qdrant_docker"


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def _task(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(task for task in tasks if task.get("name") == name)


def test_snapshot_verification_is_secret_hidden_and_precedes_deploy() -> None:
    main = _yaml(ROLE / "tasks" / "main.yml")
    snapshot_tasks = _yaml(ROLE / "tasks" / "preupgrade-snapshot.yml")
    include_index = next(
        index
        for index, task in enumerate(main)
        if task.get("ansible.builtin.include_tasks") == "preupgrade-snapshot.yml"
    )
    deploy_index = next(index for index, task in enumerate(main) if task.get("name") == "Deploy qdrant via docker compose")
    assert include_index < deploy_index

    listing = _task(snapshot_tasks, "List Qdrant full-storage snapshots after creation")
    verification = _task(snapshot_tasks, "Verify Qdrant pre-upgrade snapshot is present and complete")
    assert listing["ansible.builtin.uri"]["method"] == "GET"
    assert listing["ansible.builtin.uri"]["url"].endswith("/snapshots")
    assert listing["ansible.builtin.uri"]["headers"]["api-key"] == "{{ qdrant_docker_api_key }}"
    assert listing["no_log"] is True
    assert verification["ansible.builtin.assert"]
    # The assert carries no secret; leaving it logged keeps its failure reason visible.
    assert "no_log" not in verification
    assert "checksum" in str(verification["ansible.builtin.assert"]["that"])
    assert "size" in str(verification["ansible.builtin.assert"]["that"])


def test_free_space_check_uses_snapshot_filesystem_and_storage_size_before_create() -> None:
    tasks = _yaml(ROLE / "tasks" / "preupgrade-snapshot.yml")
    available = _task(tasks, "Check available bytes on the Qdrant snapshots filesystem")
    storage = _task(tasks, "Measure current Qdrant storage size")
    check = _task(tasks, "Check Qdrant pre-upgrade snapshot free space")
    create = _task(tasks, "Create Qdrant full-storage pre-upgrade snapshot")

    assert available["ansible.builtin.command"]["argv"] == [
        "df",
        "--output=avail",
        "-B1",
        "{{ qdrant_docker_snapshots_dir }}",
    ]
    assert storage["ansible.builtin.command"]["argv"] == ["du", "-sb", "{{ qdrant_docker_storage_dir }}"]
    assert available["changed_when"] is False
    assert storage["changed_when"] is False
    assert check["ansible.builtin.assert"]["that"]
    assert "qdrant_docker_preupgrade_snapshot_free_space_factor" in str(check["ansible.builtin.assert"])
    assert "refusing" in check["ansible.builtin.assert"]["fail_msg"]
    assert max(tasks.index(available), tasks.index(storage), tasks.index(check)) < tasks.index(create)


def test_snapshot_timeout_and_path_defaults_are_single_sourced() -> None:
    defaults = _yaml(ROLE / "defaults" / "main.yml")
    assert defaults["qdrant_docker_preupgrade_snapshot_timeout"] == 60
    assert defaults["qdrant_docker_preupgrade_snapshot_free_space_factor"] == 1.2
    assert defaults["qdrant_docker_snapshots_dir"] == "{{ qdrant_docker_data_dir }}/snapshots"
    assert defaults["qdrant_docker_storage_dir"] == "{{ qdrant_docker_data_dir }}/storage"

    main = (ROLE / "tasks" / "main.yml").read_text()
    compose = (ROLE / "templates" / "docker-compose.yml.j2").read_text()
    assert 'path: "{{ qdrant_docker_storage_dir }}"' in main
    assert 'path: "{{ qdrant_docker_snapshots_dir }}"' in main
    assert "{{ qdrant_docker_storage_dir }}:/qdrant/storage" in compose
    assert "{{ qdrant_docker_snapshots_dir }}:/qdrant/snapshots" in compose
    assert "qdrant_docker_data_dir }}/storage" not in main + compose
    assert "qdrant_docker_data_dir }}/snapshots" not in main + compose


def test_stopped_container_skip_and_fail_modes_are_explicit() -> None:
    tasks = _yaml(ROLE / "tasks" / "preupgrade-snapshot.yml")
    defaults = _yaml(ROLE / "defaults" / "main.yml")
    assert defaults["qdrant_docker_preupgrade_snapshot_when_stopped"] == "skip"

    fail_task = _task(tasks, "Fail when qdrant is stopped and its image would change")
    skip_task = _task(tasks, "Explain skipped qdrant snapshot because the container is stopped")
    for task, mode in ((fail_task, "fail"), (skip_task, "skip")):
        conditions = task["when"]
        assert "qdrant_docker_running_check.stdout == ''" in conditions
        assert "qdrant_docker_running_image.stdout != qdrant_docker_image" in conditions
        assert f"qdrant_docker_preupgrade_snapshot_when_stopped == '{mode}'" in conditions
    assert "ansible.builtin.fail" in fail_task
    assert "proceeding with deployment" in skip_task["ansible.builtin.debug"]["msg"]


def test_no_api_key_occurrence_is_logged_by_an_unhidden_task() -> None:
    tasks = _yaml(ROLE / "tasks" / "preupgrade-snapshot.yml")
    for task in tasks:
        if "qdrant_docker_api_key" in yaml.safe_dump(task):
            assert task.get("no_log") is True, task.get("name")
        assert task.get("ignore_errors") is not True, task.get("name")


def test_role_readme_documents_full_storage_restore_and_root_links_it() -> None:
    role_readme = (ROLE / "README.md").read_text()
    root_readme = (REPO_ROOT / "README.md").read_text()
    assert "## Restoring a pre-upgrade snapshot" in role_readme
    assert "GET /snapshots" in role_readme
    assert "--storage-snapshot <path>" in role_readme
    assert "snapshot recover API" in role_readme
    assert "[`qdrant_docker`](roles/qdrant_docker/README.md)" in root_readme
