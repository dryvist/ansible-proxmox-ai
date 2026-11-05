"""The Postgres data dir is owned by the postgres image's uid (999) with mode 0700."""

from pathlib import Path

import yaml


ROLE = Path(__file__).resolve().parents[2] / "roles/langfuse_docker"
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/00-main.yml").read_text(encoding="utf-8"))
POSTGRES_PATH = "{{ langfuse_docker_postgres_data_dir }}"


def test_postgres_data_dir_is_owned_by_uid_999_with_mode_0700():
    entries = [
        entry for entry in DEFAULTS["langfuse_docker_directories"]
        if entry["path"] == POSTGRES_PATH
    ]

    assert len(entries) == 1
    entry = entries[0]
    assert entry["owner"] == "999"
    assert entry["group"] == "999"
    assert entry["mode"] == "0700"
