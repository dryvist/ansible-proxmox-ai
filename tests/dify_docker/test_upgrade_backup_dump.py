"""Dify upgrades refuse a truncated PostgreSQL backup before stopping the stack."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
STORAGE_TASKS = ROOT / "roles/dify_docker/tasks/upgrade_backup_storage.yml"
MARKER_TASK = "Require each PostgreSQL pre-upgrade backup to end with its dump-complete marker"
TAIL_TASK = "Read the last lines of each PostgreSQL pre-upgrade backup"


def _tasks() -> list[dict]:
    return yaml.safe_load(STORAGE_TASKS.read_text(encoding="utf-8"))


def _names() -> list[str]:
    return [task["name"] for task in _tasks()]


def test_dump_marker_check_follows_the_size_check_and_reads_every_backup():
    names = _names()
    assert names.index("Require non-empty PostgreSQL pre-upgrade backups") < names.index(TAIL_TASK)
    assert names.index(TAIL_TASK) < names.index(MARKER_TASK)
    tail = next(task for task in _tasks() if task["name"] == TAIL_TASK)
    assert tail["loop"] == "{{ dify_docker_postgres_backup_paths }}"
    assert tail["changed_when"] is False


def test_dump_marker_matches_pg_dump_and_pg_dumpall_but_not_a_truncated_file():
    marker = next(task for task in _tasks() if task["name"] == MARKER_TASK)
    expression = marker["ansible.builtin.assert"]["that"][0]
    pattern = re.search(r"search\('(.+)'\)", expression).group(1)
    assert re.search(pattern, "--\n-- PostgreSQL database dump complete\n--\n")
    assert re.search(pattern, "--\n-- PostgreSQL database cluster dump complete\n--\n")
    assert not re.search(pattern, "COPY public.messages (id, query) FROM stdin;\n1\thello")
