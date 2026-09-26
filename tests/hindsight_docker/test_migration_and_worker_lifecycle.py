"""Hindsight 0.10.x readiness: out-of-band migration, stale-worker
decommission, explicit wall-clock timeouts, and the single-sourced DB URL.

These render the compose template via the shared _compose_render helper
(matching test_retain_llm_scope.py) and parse the role task YAML directly —
no live Ansible run, no Docker. What each covers is stated on the test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from _compose_render import DEFAULT_CONTEXT, env_line, render

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/hindsight_docker"
SITE_YML = REPO_ROOT / "playbooks/site.yml"


def _load_role_tasks(name: str) -> list[dict]:
    return yaml.safe_load((ROLE_ROOT / "tasks" / name).read_text())


def test_migrations_on_startup_defaults_off_in_compose() -> None:
    rendered = render()
    assert '"false"' in env_line(rendered, "HINDSIGHT_API_RUN_MIGRATIONS_ON_STARTUP")


def test_wall_clock_timeouts_render_explicitly_and_below_upstream_defaults() -> None:
    rendered = render()
    # Upstream loose defaults are 3600 (retain) / 7200 (consolidation) —
    # every value here must render explicitly and stay strictly under them.
    assert env_line(rendered, "HINDSIGHT_API_RETAIN_WALL_TIMEOUT").endswith('"720"')
    assert env_line(rendered, "HINDSIGHT_API_CONSOLIDATION_WALL_TIMEOUT").endswith('"2400"')
    assert env_line(rendered, "HINDSIGHT_API_REFLECT_WALL_TIMEOUT").endswith('"120"')
    assert env_line(rendered, "HINDSIGHT_API_REFRESH_MENTAL_MODEL_WALL_TIMEOUT").endswith('"120"')
    for name, value in (
        ("HINDSIGHT_API_RETAIN_WALL_TIMEOUT", 720),
        ("HINDSIGHT_API_CONSOLIDATION_WALL_TIMEOUT", 2400),
    ):
        assert value < 3600 if "RETAIN" in name else value < 7200


def test_database_url_is_single_sourced_not_rebuilt_in_the_template() -> None:
    # The compose template must interpolate hindsight_docker_db_url as a
    # whole, never rebuild the DSN from the individual db_user/password/
    # host/port/name parts (those still exist as the inputs db_url is
    # composed from in defaults/main.yml, but the template itself must not
    # duplicate that composition — one source, per the org's
    # one-base-variable-per-consumer rule).
    template_source = (ROLE_ROOT / "templates/docker-compose.yml.j2").read_text()
    assert "hindsight_docker_db_url" in template_source
    assert "hindsight_docker_db_user }}:{{ hindsight_docker_db_password" not in template_source
    rendered = render()
    assert DEFAULT_CONTEXT["hindsight_docker_db_url"] in env_line(rendered, "HINDSIGHT_API_DATABASE_URL")


def test_defaults_derive_db_url_from_the_same_parts_the_template_used_to_inline() -> None:
    defaults_source = (ROLE_ROOT / "defaults/main.yml").read_text()
    assert "hindsight_docker_db_url" in defaults_source
    # Derived, not a second hand-typed literal: every part still feeds it.
    for part in (
        "hindsight_docker_db_user",
        "hindsight_docker_db_password",
        "hindsight_docker_db_host",
        "hindsight_docker_db_port",
        "hindsight_docker_db_name",
    ):
        assert part in defaults_source.split("hindsight_docker_db_url:")[1].split("\n\n")[0]


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


def test_compose_template_sources_vector_extension_from_the_shared_var() -> None:
    # run-db-migration reconciles vector/text-search indexes against
    # HINDSIGHT_API_VECTOR_EXTENSION — a literal here and a different literal
    # (or var) in the migration task would silently disagree.
    template_source = (ROLE_ROOT / "templates/docker-compose.yml.j2").read_text()
    assert "HINDSIGHT_API_VECTOR_EXTENSION: \"{{ hindsight_docker_vector_extension }}\"" in template_source
    rendered = render()
    assert env_line(rendered, "HINDSIGHT_API_VECTOR_EXTENSION").endswith('"pgvector"')


def test_decommission_task_extracts_worker_ids_and_diffs_against_inventory() -> None:
    tasks = _load_role_tasks("decommission_stale_workers.yml")
    fact = next(t for t in tasks if t["name"].startswith("Determine worker ids"))
    expr = fact["ansible.builtin.set_fact"]["hindsight_docker_stale_worker_ids"]
    assert "regex_findall('^Worker: (\\S+) '" in expr
    assert "difference(groups['hindsight_group'])" in expr

    listing = next(t for t in tasks if t["name"].startswith("List Hindsight workers"))
    list_argv = listing["ansible.builtin.command"]["argv"]
    assert "HINDSIGHT_API_DATABASE_URL" in list_argv
    assert not any("hindsight_docker_db_url" in a for a in list_argv)
    assert listing["environment"] == {"HINDSIGHT_API_DATABASE_URL": "{{ hindsight_docker_db_url }}"}

    decommission = next(t for t in tasks if t["name"].startswith("Decommission worker ids"))
    argv = decommission["ansible.builtin.command"]["argv"]
    # Singular, per-id decommission-worker only — decommission-workers
    # (plural) releases every worker unconditionally, including live ones.
    assert argv[-3:] == ["decommission-worker", "{{ item }}", "--yes"]
    assert "HINDSIGHT_API_DATABASE_URL" in argv
    assert not any("hindsight_docker_db_url" in a for a in argv)
    assert decommission["environment"] == {"HINDSIGHT_API_DATABASE_URL": "{{ hindsight_docker_db_url }}"}
    assert decommission["loop"] == "{{ hindsight_docker_stale_worker_ids | default([]) }}"


def test_site_yml_imports_the_hindsight_play() -> None:
    # The play itself was split into playbooks/hindsight.yml to keep site.yml
    # under its token budget (.token-limits.yaml) — same pattern already
    # used there for agents.yml, llm-serving.yml, and phoenix.yml.
    site = yaml.safe_load(SITE_YML.read_text())
    hindsight_entry = next(p for p in site if p.get("name") == "Configure Hindsight agent-memory service")
    assert hindsight_entry["import_playbook"] == "hindsight.yml"


def test_site_yml_wires_both_lifecycle_tasks_before_the_isolated_block() -> None:
    hindsight_play = yaml.safe_load((REPO_ROOT / "playbooks/hindsight.yml").read_text())[0]
    assert hindsight_play["max_fail_percentage"] == 0
    pre_task_names = [t["name"] for t in hindsight_play["pre_tasks"]]
    gates_task = next(t for t in hindsight_play["pre_tasks"] if t["name"].startswith("Run Hindsight"))
    assert gates_task["ansible.builtin.import_tasks"] == "tasks/hindsight_lifecycle_gates.yml"
    # Precedes the role's normal (rescue-wrapped) entry, after the pool gate.
    pool_gate_index = pre_task_names.index("Gate on pool-member reachability")
    gates_index = pre_task_names.index(gates_task["name"])
    assert pool_gate_index < gates_index


def test_lifecycle_gates_file_uses_include_role_so_role_defaults_resolve() -> None:
    # include_role (not import_tasks on the role's raw task path) is
    # load-bearing: it is what makes the role's own defaults/main.yml (image
    # tag, db_url, the pre-migration backup dir) resolve before these fire in
    # pre_tasks, ahead of `tasks:` where the role is otherwise entered.
    gates = yaml.safe_load((REPO_ROOT / "playbooks/tasks/hindsight_lifecycle_gates.yml").read_text())
    names = [t["name"] for t in gates]
    assert "Run the Hindsight database migration once, before any replica redeploys" in names
    assert "Decommission Hindsight worker ids no longer present in inventory" in names
    migration = next(t for t in gates if t["name"].startswith("Run the Hindsight database migration"))
    assert migration["ansible.builtin.include_role"] == {
        "name": "hindsight_docker",
        "tasks_from": "run_db_migration.yml",
    }
    decommission = next(t for t in gates if t["name"].startswith("Decommission Hindsight worker ids"))
    assert decommission["ansible.builtin.include_role"] == {
        "name": "hindsight_docker",
        "tasks_from": "decommission_stale_workers.yml",
    }


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
