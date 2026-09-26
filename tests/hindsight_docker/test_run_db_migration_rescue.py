"""roles/hindsight_docker/tasks/run_db_migration_drain_and_migrate.yml: the
rescue-selection logic (B2) that decides, after a backup/integrity/migration
failure inside the block, whether it is safe to restart the drained
replicas on their current image or whether the pool must stay down. Parses
the role task YAML directly and evaluates its Jinja expressions — no live
Ansible run, no Docker.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/hindsight_docker"


def _load_rescue_tasks() -> list[dict]:
    doc = yaml.safe_load((ROLE_ROOT / "tasks" / "run_db_migration_drain_and_migrate.yml").read_text())
    return doc[0]["rescue"]


def _render_unchanged(**ctx) -> bool:
    tasks = _load_rescue_tasks()
    fact = next(t for t in tasks if t["name"] == "Determine whether the failed attempt left the schema unchanged")
    expr = fact["ansible.builtin.set_fact"]["hindsight_docker_schema_unchanged"]
    rendered = jinja2.Environment().from_string(expr).render(**ctx)
    assert rendered in ("True", "False")
    return rendered == "True"


def test_backup_failure_never_attempted_migration_is_always_unchanged() -> None:
    # Fixture (a): the backup command itself failed, so the migration task
    # never ran and never registered a result — always safe to restart,
    # regardless of anything else (the "or" short-circuits on this alone).
    assert _render_unchanged(hindsight_docker_migration_attempted=False) is True


def test_integrity_failure_never_attempted_migration_is_always_unchanged() -> None:
    # Fixture (b): the zip-integrity check failing also stops the block
    # before the migration task runs — same as (a).
    assert _render_unchanged(hindsight_docker_migration_attempted=False) is True


def test_migrate_fail_with_unchanged_revision_restarts() -> None:
    # Fixture (c): migration ran and failed, but the recovery probe reads
    # back the SAME revision the pre-migration probe recorded.
    assert (
        _render_unchanged(
            hindsight_docker_migration_attempted=True,
            hindsight_docker_recovery_revision_probe={"rc": 0, "stdout": "b3e8d1c6f4a9"},
            hindsight_docker_current_revision_probe={"stdout": "b3e8d1c6f4a9"},
        )
        is True
    )


def test_migrate_fail_with_advanced_revision_never_restarts() -> None:
    # Fixture (d): migration ran, failed partway, and the recovery probe now
    # reads a DIFFERENT revision than the pre-attempt one.
    assert (
        _render_unchanged(
            hindsight_docker_migration_attempted=True,
            hindsight_docker_recovery_revision_probe={"rc": 0, "stdout": "c9f1a2b3d4e5"},
            hindsight_docker_current_revision_probe={"stdout": "b3e8d1c6f4a9"},
        )
        is False
    )


def test_recovery_probe_itself_failing_is_treated_as_changed_never_guessed_safe() -> None:
    # A failure to even re-read the post-failure revision (bad rc) must
    # never be read as "unchanged" — a restart is never guessed to be safe.
    assert (
        _render_unchanged(
            hindsight_docker_migration_attempted=True,
            hindsight_docker_recovery_revision_probe={"rc": 1, "stdout": ""},
            hindsight_docker_current_revision_probe={"stdout": "b3e8d1c6f4a9"},
        )
        is False
    )


def test_restart_task_only_targets_deployed_replicas_and_is_gated_on_unchanged() -> None:
    tasks = _load_rescue_tasks()
    restart = next(t for t in tasks if t["name"].startswith("Restart every replica"))
    assert restart["community.docker.docker_compose_v2"] == {
        "project_src": "{{ hindsight_docker_data_dir }}",
        "state": "started",
    }
    assert restart["loop"] == "{{ hindsight_docker_compose_exists_check.results | default([]) }}"
    assert restart["delegate_to"] == "{{ hindsight_docker_replica_check.item }}"
    assert restart.get("run_once") is True
    assert restart["when"] == (
        "hindsight_docker_schema_unchanged and (hindsight_docker_replica_check.stat.exists | default(false))"
    )


def test_rescue_always_re_raises_so_the_play_still_fails() -> None:
    # B2's hard requirement: recovery must not mask the error — the last
    # rescue task is an unconditional fail, in both branches.
    tasks = _load_rescue_tasks()
    reraise = tasks[-1]
    assert reraise["name"] == "Re-raise so the play still fails after recovery"
    assert "ansible.builtin.fail" in reraise
    assert "when" not in reraise
    assert reraise.get("run_once") is True

    template = jinja2.Environment().from_string(reraise["ansible.builtin.fail"]["msg"])
    unchanged_msg = template.render(hindsight_docker_schema_unchanged=True)
    changed_msg = template.render(
        hindsight_docker_schema_unchanged=False,
        hindsight_docker_premigration_backup_filename="pre-migration-a1-to-b2-20260101T000000.zip",
        hindsight_docker_migration_backup_dir="/backup",
    )
    assert "restarted on its current image" in unchanged_msg
    assert "Restore from pre-migration-a1-to-b2-20260101T000000.zip in /backup" in changed_msg


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
