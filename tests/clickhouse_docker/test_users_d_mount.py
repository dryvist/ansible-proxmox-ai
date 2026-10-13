"""Check the users.d mount the image entrypoint writes into."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/clickhouse_docker"


def test_users_d_is_mounted_writable_for_the_entrypoint():
    compose = (ROLE / "templates/docker-compose.yml.j2").read_text()
    mounts = [line.strip() for line in compose.splitlines() if "/etc/clickhouse-server/users.d" in line]

    assert mounts == ["- {{ clickhouse_docker_users_d_dir }}:/etc/clickhouse-server/users.d"]


def test_users_d_is_owned_by_the_container_user():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    entries = [
        entry
        for value in defaults.values()
        if isinstance(value, list)
        for entry in value
        if isinstance(entry, dict) and entry.get("path") == "{{ clickhouse_docker_users_d_dir }}"
    ]

    assert len(entries) == 1
    assert entries[0]["owner"] == "{{ clickhouse_docker_uid }}"
    assert entries[0]["group"] == "{{ clickhouse_docker_uid }}"
