"""Bind mounts for non-root Dify images are owned by the uid that writes them."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
STORAGE_TASKS = ROOT / "roles/dify_docker/tasks/storage_and_secret.yml"
PLUGIN_DIR = "{{ dify_docker_data_dir }}/plugin_daemon"


def _tasks() -> list[dict]:
    return yaml.safe_load(STORAGE_TASKS.read_text(encoding="utf-8"))


def _task(name: str) -> dict:
    return next(task for task in _tasks() if task.get("name") == name)


def test_plugin_daemon_storage_is_not_reset_to_root():
    root_owned = _task("Create Dify data subdirectories")
    assert root_owned["ansible.builtin.file"]["owner"] == "root"
    assert PLUGIN_DIR not in root_owned["loop"]


def test_plugin_daemon_storage_belongs_to_the_daemon_uid():
    task = _task("Create the Dify plugin daemon storage directory (plugin daemon runs as uid 1001)")
    spec = task["ansible.builtin.file"]
    assert spec["path"] == PLUGIN_DIR
    assert spec["state"] == "directory"
    assert spec["owner"] == "1001" and spec["group"] == "1001"
    # Recursive, so packages an earlier root-run daemon wrote become writable.
    assert spec["recurse"] is True
    assert spec["mode"] == "u=rwX,g=rX,o="
