"""Back up an existing schema using its identified immutable source image."""

from pathlib import Path
import re

import jinja2
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/hindsight_docker"


def _block():
    tasks = yaml.safe_load((ROLE / "tasks/run_db_migration_drain_and_migrate.yml").read_text())
    return next(task["block"] for task in tasks if "block" in task and "rescue" in task)


def _selection(probe, fallback=""):
    task = next(task for task in _block() if "hindsight_docker_backup_image" in task.get("ansible.builtin.set_fact", {}))
    expr = task["ansible.builtin.set_fact"]["hindsight_docker_backup_image"]
    return jinja2.Environment().from_string(expr).render(
        hindsight_docker_deployed_image_probe=probe,
        hindsight_docker_migration_source_image=fallback,
    ).strip()


def _accepted(image):
    task = next(task for task in _block() if task["name"].startswith("Require an identified immutable source image"))
    expression = task["ansible.builtin.assert"]["that"][0]
    env = jinja2.Environment()
    env.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
    return bool(env.compile_expression(expression)(hindsight_docker_backup_image=image))


def test_source_probe_uses_the_actual_container_image_id():
    tasks = _block()
    probe = next(task for task in tasks if task.get("register") == "hindsight_docker_deployed_image_probe")
    assert any(".Image" in part for part in probe["ansible.builtin.command"]["argv"])
    assert not any(".Config.Image" in part for part in probe["ansible.builtin.command"]["argv"])
    backup = next(task for task in tasks if task["name"] == "Take a pre-migration database backup")
    assert "{{ hindsight_docker_backup_image }}" in backup["ansible.builtin.command"]["argv"]
    gate = next(task for task in tasks if task["name"].startswith("Require an identified immutable source image"))
    assert tasks.index(probe) < tasks.index(gate) < tasks.index(backup)


def test_detected_image_takes_precedence_over_explicit_fallback():
    image_id = "sha256:" + "a" * 64
    assert _selection({"rc": 0, "stdout": image_id + "\n"}, "sha256:" + "b" * 64) == image_id
    assert _accepted(image_id)


@pytest.mark.parametrize("probe", [{"rc": 1, "stdout": ""}, {"rc": 0, "stdout": ""}, {"skipped": True}])
def test_missing_source_requires_explicit_immutable_fallback(probe):
    fallback = "ghcr.io/example/hindsight:old@sha256:" + "c" * 64
    assert _selection(probe, fallback) == fallback
    assert _accepted(fallback)
    assert _selection(probe) == ""
    assert not _accepted(_selection(probe))


@pytest.mark.parametrize("image", ["", "ghcr.io/example/hindsight:old", "sha256:bad"])
def test_mutable_or_missing_source_is_rejected(image):
    assert not _accepted(image)


def test_default_does_not_guess_an_old_image():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert defaults["hindsight_docker_migration_source_image"] == ""
