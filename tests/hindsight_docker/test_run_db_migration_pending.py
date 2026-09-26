"""roles/hindsight_docker/tasks/run_db_migration.yml: the pre-migration
backup and the migration itself must both run only when a migration is
actually pending (no schema yet, or the database's current Alembic revision
differs from the image's target head) — split out of
test_migration_and_worker_lifecycle.py to stay under .token-limits.yaml's
per-file budget. Parses the role task YAML directly and evaluates its Jinja
expressions — no live Ansible run, no Docker.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/hindsight_docker"


def _load_role_tasks(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_ROOT / "tasks" / name).read_text())


def test_migration_task_takes_an_automated_pre_migration_backup() -> None:
    # No human confirmation gate: every converge takes its own fresh backup
    # via the vendored `hindsight-admin backup` command, then refuses to
    # migrate unless that backup file actually exists and is non-empty.
    tasks = _load_role_tasks("run_db_migration.yml")
    names = [t["name"] for t in tasks]
    assert "Take a pre-migration database backup" in names
    assert "Assert the pre-migration backup succeeded" in names

    backup = next(t for t in tasks if t["name"] == "Take a pre-migration database backup")
    argv = backup["ansible.builtin.command"]["argv"]
    assert argv[-3:] == ["hindsight-admin", "backup", "/backup/pre-migration.zip"]
    # Bare -e (no inline value): the DSN is passed via `environment:`, never
    # argv, so it never appears in `ps` output on the host.
    assert "HINDSIGHT_API_DATABASE_URL" in argv
    assert not any("hindsight_docker_db_url" in a for a in argv)
    assert backup["environment"] == {"HINDSIGHT_API_DATABASE_URL": "{{ hindsight_docker_db_url }}"}
    assert "{{ hindsight_docker_migration_backup_dir }}:/backup" in argv
    assert backup.get("run_once") is True

    gate = next(t for t in tasks if t["name"] == "Assert the pre-migration backup succeeded")
    assert gate["ansible.builtin.assert"]["that"] == [
        "hindsight_docker_premigration_backup_stat.stat.exists",
        "hindsight_docker_premigration_backup_stat.stat.size > 0",
    ]
    assert gate.get("run_once") is True

    # The migration task itself must come after the backup gate, not before.
    assert names.index(gate["name"]) < names.index("Run the Hindsight database migration once, using the target image")


def test_schema_probe_positively_detects_a_first_deploy() -> None:
    # A first-ever deploy has no schema yet, so there is nothing to back up
    # before run-db-migration creates it. Detected positively by probing for
    # Alembic's own tracker table — not by pattern-matching a backup failure
    # message, which would also swallow a real partially-broken schema.
    tasks = _load_role_tasks("run_db_migration.yml")

    probe = next(t for t in tasks if t["name"].startswith("Probe whether the Alembic"))
    argv = probe["ansible.builtin.command"]["argv"]
    assert argv[-1] == "SELECT to_regclass('public.alembic_version') IS NOT NULL"
    assert not any("hindsight_docker_db_password" in a for a in argv)
    assert probe["environment"] == {"PGPASSWORD": "{{ hindsight_docker_db_password }}"}
    assert probe.get("run_once") is True
    # An inconclusive probe (bad rc, or output that isn't a clean t/f) must
    # fail the play — never silently fall through to "no schema" and skip a
    # backup that may have been needed.
    assert probe["failed_when"] == (
        "hindsight_docker_schema_probe.rc != 0 "
        "or (hindsight_docker_schema_probe.stdout | default('') | trim) not in ['t', 'f']"
    )

    fact_task = next(t for t in tasks if t["name"] == "Record whether an existing schema was found")
    expr = fact_task["ansible.builtin.set_fact"]["hindsight_docker_schema_exists"]

    # Behavioral, not just structural: actually evaluate the Jinja expression
    # the role runs, for both probe outcomes.
    import jinja2

    jinja_env = jinja2.Environment()
    template = jinja_env.from_string(expr)
    render = lambda stdout: template.render(hindsight_docker_schema_probe={"stdout": stdout})  # noqa: E731
    assert render("t") == "True"  # tracker present -> existing schema
    assert render("f") == "False"  # tracker absent -> first deploy


def test_backup_runs_only_when_a_schema_exists_and_a_migration_is_pending() -> None:
    tasks = _load_role_tasks("run_db_migration.yml")

    backup = next(t for t in tasks if t["name"] == "Take a pre-migration database backup")
    # Schema alone isn't enough: an already-migrated database must take no
    # backup either, or the second Molecule converge (idempotence) reports
    # this task as changed for work that accomplishes nothing.
    assert backup["when"] == "hindsight_docker_schema_exists and hindsight_docker_migration_pending"
    # No failed_when override: a real backup failure (schema present, write
    # error, connection drop, anything) uses ansible.builtin.command's
    # default behavior and fails the play — the stderr-tolerance this once
    # had is gone entirely.
    assert "failed_when" not in backup
    assert "register" not in backup
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


def test_migration_pending_probes_use_the_vendored_alembic_cli_no_script() -> None:
    # The database side: once a schema exists, its current revision comes
    # from the same tracker table the existence probe already checked —
    # same psql -tAc convention, same fail-closed discipline (a connection
    # error or a tracker with anything other than exactly one row fails the
    # play rather than being read as "no current revision").
    tasks = _load_role_tasks("run_db_migration.yml")

    current = next(t for t in tasks if t["name"] == "Probe the database's current Alembic revision")
    argv = current["ansible.builtin.command"]["argv"]
    assert argv[-1] == "SELECT version_num FROM public.alembic_version"
    assert not any("hindsight_docker_db_password" in a for a in argv)
    assert current["environment"] == {"PGPASSWORD": "{{ hindsight_docker_db_password }}"}
    assert current["when"] == "hindsight_docker_schema_exists"
    assert current["failed_when"] == (
        "hindsight_docker_current_revision_probe.rc != 0 "
        "or (hindsight_docker_current_revision_probe.stdout_lines | default([]) | length) != 1"
    )

    # The image side: no database connection, no custom script — the image's
    # own vendored `alembic` CLI (bundled alongside `hindsight-admin`,
    # confirmed at both the v0.9.0 and v0.10.1 tags) reports its script
    # directory's head via its native `heads` subcommand, against a
    # one-line generated config (alembic requires script_location from a
    # config file; the CLI has no flag for it).
    ini_task = next(
        t for t in tasks if t["name"] == "Generate the alembic config used to read the image's target head revision"
    )
    assert ini_task["ansible.builtin.copy"]["content"] == (
        "[alembic]\nscript_location = {{ hindsight_docker_alembic_script_location }}\n"
    )

    head = next(t for t in tasks if t["name"] == "Determine the target head revision from the image")
    argv = head["ansible.builtin.command"]["argv"]
    assert argv[-4:] == ["alembic", "-c", "/tmp/alembic-heads.ini", "heads"]
    assert "{{ hindsight_docker_migration_backup_dir }}/alembic-heads.ini:/tmp/alembic-heads.ini:ro" in argv
    assert "{{ hindsight_docker_image }}" in argv
    assert "when" not in head  # always readable: no database connection required
    assert head["failed_when"] == (
        "hindsight_docker_head_revision_probe.rc != 0 "
        "or (hindsight_docker_head_revision_probe.stdout_lines | default([]) | length) != 1"
    )

    head_fact = next(t for t in tasks if t["name"] == "Record the image's target head revision")
    expr = head_fact["ansible.builtin.set_fact"]["hindsight_docker_target_head_revision"]

    import jinja2

    jinja_env = jinja2.Environment()
    template = jinja_env.from_string(expr)
    assert template.render(hindsight_docker_head_revision_probe={"stdout": "b3e8d1c6f4a9 (head)\n"}) == "b3e8d1c6f4a9"


def test_migration_pending_fact_covers_fresh_behind_and_up_to_date() -> None:
    # Behavioral, not just structural: actually evaluate the Jinja expression
    # the role runs, for all three real states.
    tasks = _load_role_tasks("run_db_migration.yml")
    pending_fact = next(t for t in tasks if t["name"] == "Determine whether a migration is pending")
    expr = pending_fact["ansible.builtin.set_fact"]["hindsight_docker_migration_pending"]

    import jinja2

    jinja_env = jinja2.Environment()
    template = jinja_env.from_string(expr)

    # Fresh deploy: no schema at all. The "or" must short-circuit before
    # touching the current-revision probe or the target-head fact — neither
    # is passed here, so a non-short-circuiting expression would raise
    # jinja2.UndefinedError instead of rendering "True".
    assert template.render(hindsight_docker_schema_exists=False) == "True"

    # Existing schema, already at head: nothing pending.
    assert (
        template.render(
            hindsight_docker_schema_exists=True,
            hindsight_docker_current_revision_probe={"stdout": "b3e8d1c6f4a9"},
            hindsight_docker_target_head_revision="b3e8d1c6f4a9",
        )
        == "False"
    )

    # Existing schema, behind head: pending.
    assert (
        template.render(
            hindsight_docker_schema_exists=True,
            hindsight_docker_current_revision_probe={"stdout": "f2a6d8c4b1e9"},
            hindsight_docker_target_head_revision="b3e8d1c6f4a9",
        )
        == "True"
    )


def test_migration_changed_when_reflects_whether_alembic_actually_upgraded() -> None:
    # A no-op run-db-migration (already at head) must report changed=false,
    # or Molecule's idempotence check fails on a task that did nothing —
    # confirmed at the pinned v0.9.0 tag: a real upgrade logs "Running
    # upgrade <rev> -> <rev>, ..." per revision to stderr, a no-op run logs
    # none. Evaluated with the real Jinja expression, not a string match.
    tasks = _load_role_tasks("run_db_migration.yml")
    migrate = next(t for t in tasks if "Run the Hindsight database migration" in t["name"])
    assert migrate["register"] == "hindsight_docker_migration_result"

    import jinja2

    jinja_env = jinja2.Environment()
    template = jinja_env.from_string("{{ " + migrate["changed_when"] + " }}")
    render = lambda stderr: template.render(  # noqa: E731
        hindsight_docker_migration_result={"stderr": stderr}
    )
    assert render("Running upgrade a1b2 -> c3d4, add a column.\n") == "True"
    assert render("Database migrations completed successfully for 1 schema(s)") == "False"


def test_migration_task_runs_against_the_target_image_using_the_shared_db_url() -> None:
    tasks = _load_role_tasks("run_db_migration.yml")
    migrate = next(t for t in tasks if "Run the Hindsight database migration" in t["name"])
    argv = migrate["ansible.builtin.command"]["argv"]
    assert argv[-2:] == ["hindsight-admin", "run-db-migration"]
    assert "{{ hindsight_docker_image }}" in argv
    assert "HINDSIGHT_API_DATABASE_URL" in argv
    assert "HINDSIGHT_API_VECTOR_EXTENSION" in argv
    assert not any("hindsight_docker_db_url" in a for a in argv)
    assert migrate["environment"] == {
        "HINDSIGHT_API_DATABASE_URL": "{{ hindsight_docker_db_url }}",
        "HINDSIGHT_API_VECTOR_EXTENSION": "{{ hindsight_docker_vector_extension }}",
    }
    assert migrate.get("run_once") is True
    # Skipped entirely once nothing is pending, so it can't be flagged
    # non-idempotent for a run-db-migration invocation that would have been
    # a real no-op anyway.
    assert migrate["when"] == "hindsight_docker_migration_pending"


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
