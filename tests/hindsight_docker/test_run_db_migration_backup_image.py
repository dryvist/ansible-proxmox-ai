"""The pre-migration backup runs on the image the current schema was deployed
with, falling back to the pinned image only when no deployed container answers."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

TASKS = Path(__file__).resolve().parents[2] / "roles/hindsight_docker/tasks/run_db_migration_drain_and_migrate.yml"


def _block() -> list[dict]:
    return yaml.safe_load(TASKS.read_text())[0]["block"]


def _image_arg() -> str:
    backup = next(t for t in _block() if t["name"] == "Take a pre-migration database backup")
    argv = backup["ansible.builtin.command"]["argv"]
    return argv[argv.index("hindsight-admin") - 1]


def _render(**ctx: object) -> str:
    return jinja2.Environment().from_string(_image_arg()).render(hindsight_docker_image="pinned:new", **ctx).strip()


def test_probe_reads_the_deployed_container_image_before_the_backup() -> None:
    names = [t["name"] for t in _block()]
    assert names.index("Read the image the current schema was deployed with") < names.index(
        "Take a pre-migration database backup"
    )


def test_backup_uses_the_deployed_image() -> None:
    probe = {"rc": 0, "stdout": "registry/hindsight:old\n"}
    assert _render(hindsight_docker_deployed_image_probe=probe) == "registry/hindsight:old"


def test_backup_falls_back_to_the_pinned_image() -> None:
    assert _render(hindsight_docker_deployed_image_probe={"rc": 1, "stdout": ""}) == "pinned:new"
    assert _render(hindsight_docker_deployed_image_probe={"rc": 0, "stdout": ""}) == "pinned:new"
    # A skipped probe still registers, without rc or stdout.
    assert _render(hindsight_docker_deployed_image_probe={"skipped": True}) == "pinned:new"
