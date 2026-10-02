"""roles/hindsight_docker/tasks/run_db_migration.yml: the migration itself
must run only when actually pending (no schema yet, or the database's
current Alembic revision differs from the image's target head), and the
head/current-revision probes it depends on. The backup itself (integrity,
naming, timeout) and the whole-fleet drain live in
test_run_db_migration_backup_safety.py — split out (this file was itself
split out of test_migration_and_worker_lifecycle.py earlier) to stay under
.token-limits.yaml's per-file budget. Parses the role task YAML directly and
evaluates its Jinja expressions — no live Ansible run, no Docker.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/hindsight_docker"


def _load_role_tasks(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_ROOT / "tasks" / name).read_text())


def _load_drain_tasks() -> list[dict]:
    doc = _load_role_tasks("run_db_migration_drain_and_migrate.yml")
    return doc[0]["block"]


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
    tasks = _load_drain_tasks()
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
    tasks = _load_drain_tasks()
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
    # S3/S4: bounded, and output withheld (DSN in a connection error) behind
    # a dedicated rc-only follow-up assert.
    assert migrate["no_log"] is True
    assert migrate["failed_when"] is False
    assert migrate["async"] == "{{ hindsight_docker_migration_wall_timeout_seconds }}"
    assert migrate["poll"] == 5

    tasks_by_name = {t["name"]: t for t in tasks}
    migration_gate = tasks_by_name["Fail loudly if the database migration did not exit cleanly"]
    assert migration_gate["ansible.builtin.assert"]["that"] == [
        "hindsight_docker_migration_result.rc | default(-1) == 0",
        "not (hindsight_docker_migration_result.failed | default(false))",
    ]
    assert migration_gate["when"] == "hindsight_docker_migration_pending"


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
