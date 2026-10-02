"""roles/hindsight_docker/tasks/run_db_migration_drain_and_migrate.yml: the
pre-migration backup itself (integrity, naming, timeout) and the
whole-fleet drain before a pending migration — split out of
test_run_db_migration_pending.py, which covers the pending/probe/
changed_when side, to stay under .token-limits.yaml's per-file budget.
Parses the role task YAML directly and evaluates its Jinja expressions —
no live Ansible run, no Docker. The rescue-selection logic (B2: restart on
failure) has its own tests in test_run_db_migration_rescue.py.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/hindsight_docker"


def _load_role_tasks(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_ROOT / "tasks" / name).read_text())


def _load_drain_tasks() -> list[dict]:
    # run_db_migration.yml's drain/backup/verify/migrate sequence is its own
    # file (token budget) and its own block: (B2 rescue) — this unwraps it
    # back to a flat task list so existing name-based lookups keep working.
    doc = _load_role_tasks("run_db_migration_drain_and_migrate.yml")
    return doc[0]["block"]


def test_migration_task_takes_an_automated_pre_migration_backup() -> None:
    # No human confirmation gate: every converge takes its own fresh backup
    # via the vendored `hindsight-admin backup` command, then refuses to
    # migrate unless that backup file actually exists, is non-empty, and is
    # a valid zip (S1/S2: filename carries the from->to revisions plus a
    # timestamp so a second migration never overwrites the first backup).
    tasks = _load_drain_tasks()
    names = [t["name"] for t in tasks]
    assert "Take a pre-migration database backup" in names
    assert "Assert the pre-migration backup succeeded" in names
    assert "Verify the pre-migration backup archive is a valid, restorable zip" in names

    backup = next(t for t in tasks if t["name"] == "Take a pre-migration database backup")
    argv = backup["ansible.builtin.command"]["argv"]
    assert argv[-3:] == [
        "hindsight-admin",
        "backup",
        "/backup/{{ hindsight_docker_premigration_backup_filename }}",
    ]
    # Bare -e (no inline value): the DSN is passed via `environment:`, never
    # argv, so it never appears in `ps` output on the host.
    assert "HINDSIGHT_API_DATABASE_URL" in argv
    assert not any("hindsight_docker_db_url" in a for a in argv)
    assert backup["environment"] == {"HINDSIGHT_API_DATABASE_URL": "{{ hindsight_docker_db_url }}"}
    assert "{{ hindsight_docker_migration_backup_dir }}:/backup" in argv
    assert backup.get("run_once") is True
    # S3/S4: bounded and its output withheld (DSN in a connection error);
    # a dedicated follow-up assert (below) keeps a real failure visible.
    assert backup["no_log"] is True
    assert backup["failed_when"] is False
    assert backup["async"] == "{{ hindsight_docker_backup_wall_timeout_seconds }}"
    assert backup["poll"] == 5

    backup_gate = next(t for t in tasks if t["name"].startswith("Fail loudly if the pre-migration backup"))
    assert backup_gate["ansible.builtin.assert"]["that"] == [
        "hindsight_docker_backup_result.rc | default(-1) == 0",
        "not (hindsight_docker_backup_result.failed | default(false))",
    ]

    integrity = next(t for t in tasks if t["name"] == "Verify the pre-migration backup archive is a valid, restorable zip")
    assert integrity["ansible.builtin.command"]["argv"] == [
        "python3",
        "-m",
        "zipfile",
        "-t",
        "{{ hindsight_docker_migration_backup_dir }}/{{ hindsight_docker_premigration_backup_filename }}",
    ]

    gate = next(t for t in tasks if t["name"] == "Assert the pre-migration backup succeeded")
    assert gate["ansible.builtin.assert"]["that"] == [
        "hindsight_docker_premigration_backup_stat.stat.exists",
        "hindsight_docker_premigration_backup_stat.stat.size > 0",
    ]
    assert gate.get("run_once") is True

    # The migration task itself must come after every backup gate, not before.
    migrate_index = names.index("Run the Hindsight database migration once, using the target image")
    for gate_name in (backup_gate["name"], integrity["name"], gate["name"]):
        assert names.index(gate_name) < migrate_index


def test_backup_filename_carries_from_to_revisions_and_a_timestamp() -> None:
    tasks = _load_drain_tasks()
    filename_fact = next(
        t for t in tasks if t["name"] == "Compute the pre-migration backup filename from the current and target revisions"
    )
    expr = filename_fact["ansible.builtin.set_fact"]["hindsight_docker_premigration_backup_filename"]
    assert "hindsight_docker_current_revision_probe.stdout" in expr
    assert "hindsight_docker_target_head_revision" in expr
    assert "now(utc=true" in expr  # not ansible_date_time: this play runs with gather_facts: false
    assert filename_fact["when"] == "hindsight_docker_schema_exists and hindsight_docker_migration_pending"


def test_backup_runs_only_when_a_schema_exists_and_a_migration_is_pending() -> None:
    tasks = _load_drain_tasks()

    backup = next(t for t in tasks if t["name"] == "Take a pre-migration database backup")
    # Schema alone isn't enough: an already-migrated database must take no
    # backup either, or the second Molecule converge (idempotence) reports
    # this task as changed for work that accomplishes nothing.
    assert backup["when"] == "hindsight_docker_schema_exists and hindsight_docker_migration_pending"
    # failed_when: false + register + a dedicated follow-up assert (S4): a
    # real backup failure still fails the play, but through an explicit
    # rc-only check rather than command's default (no_log would otherwise
    # hide the reason a bare default failure gives).
    assert backup["failed_when"] is False
    assert backup["register"] == "hindsight_docker_backup_result"
    # No Molecule-specific tag: idempotence passes on its own merit because
    # the gate above is genuinely false on an already-migrated database, not
    # because the task is hidden from the idempotence pass.
    assert "tags" not in backup

    for name in (
        "Refuse to migrate unless the pre-migration backup file is present and non-empty",
        "Assert the pre-migration backup succeeded",
    ):
        gated = next(t for t in tasks if t["name"] == name)
        assert gated["when"] == "hindsight_docker_schema_exists and hindsight_docker_migration_pending"


def test_all_already_deployed_replicas_stop_before_a_pending_migration() -> None:
    # S5: an old replica must never keep serving against the migrated schema
    # for the rest of a serial:1 rollout — every already-deployed host in
    # the group is stopped via delegate_to (looped) before the
    # backup/migrate, regardless of which single batch/host is currently
    # converging. A host with no compose project yet (first-ever deploy) is
    # skipped: nothing is running there to stop.
    tasks = _load_drain_tasks()
    check_task = next(t for t in tasks if t["name"].startswith("Check whether each replica"))
    assert check_task["ansible.builtin.stat"] == {"path": "{{ hindsight_docker_data_dir }}/docker-compose.yml"}
    assert check_task["loop"] == "{{ groups['hindsight_group'] }}"
    assert check_task["delegate_to"] == "{{ item }}"
    assert check_task.get("run_once") is True
    assert check_task["when"] == "hindsight_docker_migration_pending"

    stop_task = next(t for t in tasks if t["name"].startswith("Stop the Hindsight containers"))
    assert stop_task["community.docker.docker_compose_v2"] == {
        "project_src": "{{ hindsight_docker_data_dir }}",
        "state": "stopped",
    }
    assert stop_task["loop"] == "{{ hindsight_docker_compose_exists_check.results | default([]) }}"
    assert stop_task["delegate_to"] == "{{ hindsight_docker_replica_check.item }}"
    assert stop_task.get("run_once") is True
    assert stop_task["when"] == (
        "hindsight_docker_migration_pending "
        "and (hindsight_docker_replica_check.stat.exists | default(false))"
    )

    names = [t["name"] for t in tasks]
    backup_index = names.index("Take a pre-migration database backup")
    assert names.index(check_task["name"]) < backup_index
    assert names.index(stop_task["name"]) < backup_index


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
