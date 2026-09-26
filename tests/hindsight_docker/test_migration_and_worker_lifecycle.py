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
    # Upstream loose defaults are 3600 / 7200 / 300 — every value here must be
    # explicit and strictly under the retain/consolidation ceiling that issue
    # #4581 found too loose to protect a wedged worker slot.
    assert env_line(rendered, "HINDSIGHT_API_RETAIN_WALL_TIMEOUT").endswith('"300"')
    assert env_line(rendered, "HINDSIGHT_API_CONSOLIDATION_WALL_TIMEOUT").endswith('"900"')
    assert env_line(rendered, "HINDSIGHT_API_REFLECT_WALL_TIMEOUT").endswith('"120"')
    assert env_line(rendered, "HINDSIGHT_API_REFRESH_MENTAL_MODEL_WALL_TIMEOUT").endswith('"120"')
    for name, value in (
        ("HINDSIGHT_API_RETAIN_WALL_TIMEOUT", 300),
        ("HINDSIGHT_API_CONSOLIDATION_WALL_TIMEOUT", 900),
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


def test_migration_task_gates_on_an_explicit_backup_confirmation() -> None:
    tasks = _load_role_tasks("run_db_migration.yml")
    names = [t["name"] for t in tasks]
    assert "Refuse to run the Hindsight DB migration without a confirmed fresh backup" in names
    gate = next(t for t in tasks if "Refuse to run" in t["name"])
    assert gate["ansible.builtin.assert"]["that"] == ["hindsight_docker_migration_backup_confirmed | bool"]
    assert gate.get("run_once") is True


def test_migration_task_runs_against_the_target_image_using_the_shared_db_url() -> None:
    tasks = _load_role_tasks("run_db_migration.yml")
    migrate = next(t for t in tasks if "Run the Hindsight database migration" in t["name"])
    argv = migrate["ansible.builtin.command"]["argv"]
    assert argv[-2:] == ["hindsight-admin", "run-db-migration"]
    assert "{{ hindsight_docker_image }}" in argv
    assert any("HINDSIGHT_API_DATABASE_URL={{ hindsight_docker_db_url }}" == a for a in argv)
    assert migrate.get("run_once") is True
    assert migrate.get("no_log") is True


def test_decommission_task_extracts_worker_ids_and_diffs_against_inventory() -> None:
    tasks = _load_role_tasks("decommission_stale_workers.yml")
    fact = next(t for t in tasks if t["name"].startswith("Determine worker ids"))
    expr = fact["ansible.builtin.set_fact"]["hindsight_docker_stale_worker_ids"]
    assert "regex_findall('^Worker: (\\S+) '" in expr
    assert "difference(groups['hindsight_group'])" in expr

    decommission = next(t for t in tasks if t["name"].startswith("Decommission worker ids"))
    argv = decommission["ansible.builtin.command"]["argv"]
    # Singular, per-id decommission-worker only — decommission-workers
    # (plural) releases every worker unconditionally, including live ones.
    assert argv[-3:] == ["decommission-worker", "{{ item }}", "--yes"]
    assert decommission["loop"] == "{{ hindsight_docker_stale_worker_ids | default([]) }}"


def test_site_yml_wires_both_lifecycle_tasks_via_include_role_before_the_isolated_block() -> None:
    # include_role (not import_tasks) is load-bearing: it is what makes the
    # role's own defaults/main.yml (image tag, db_url, the backup-confirm
    # var) resolve before these fire in pre_tasks, ahead of `tasks:` where
    # the role is otherwise entered.
    site = yaml.safe_load(SITE_YML.read_text())
    hindsight_play = next(p for p in site if p.get("name") == "Configure Hindsight agent-memory service")
    assert hindsight_play["max_fail_percentage"] == 0
    pre_task_names = [t["name"] for t in hindsight_play["pre_tasks"]]
    assert "Run the Hindsight database migration once, before any replica redeploys" in pre_task_names
    assert "Decommission Hindsight worker ids no longer present in inventory" in pre_task_names
    migration_pre_task = next(
        t for t in hindsight_play["pre_tasks"] if t["name"].startswith("Run the Hindsight database migration")
    )
    assert migration_pre_task["ansible.builtin.include_role"]["tasks_from"] == "run_db_migration.yml"
    # Both lifecycle tasks precede the role's normal (rescue-wrapped) entry.
    pool_gate_index = pre_task_names.index("Gate on pool-member reachability")
    migration_index = pre_task_names.index("Run the Hindsight database migration once, before any replica redeploys")
    assert pool_gate_index < migration_index


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
