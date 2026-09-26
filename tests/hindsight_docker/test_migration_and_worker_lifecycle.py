"""Hindsight 0.10.x readiness: stale-worker decommission, explicit
wall-clock timeouts, the single-sourced DB URL, and the playbook wiring that
imports both. The pre-migration backup/migration pending-gate behavior lives
in test_run_db_migration_pending.py (split out to stay under
.token-limits.yaml's per-file budget).

These render the compose template via the shared _compose_render helper
(matching test_retain_llm_scope.py) and parse the role task YAML directly —
no live Ansible run, no Docker. What each covers is stated on the test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from _compose_render import DEFAULT_CONTEXT, env_line, render

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/hindsight_docker"
SITE_YML = REPO_ROOT / "playbooks/site.yml"
HINDSIGHT_PLAYBOOK = REPO_ROOT / "playbooks/hindsight.yml"

sys.path.insert(0, str(ROLE_ROOT / "filter_plugins"))
from hindsight_stale_workers import hindsight_stale_worker_ids  # noqa: E402


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


def test_compose_template_sources_vector_extension_from_the_shared_var() -> None:
    # run-db-migration reconciles vector/text-search indexes against
    # HINDSIGHT_API_VECTOR_EXTENSION — a literal here and a different literal
    # (or var) in the migration task would silently disagree.
    template_source = (ROLE_ROOT / "templates/docker-compose.yml.j2").read_text()
    assert "HINDSIGHT_API_VECTOR_EXTENSION: \"{{ hindsight_docker_vector_extension }}\"" in template_source
    rendered = render()
    assert env_line(rendered, "HINDSIGHT_API_VECTOR_EXTENSION").endswith('"pgvector"')


def test_decommission_task_uses_the_dead_signal_filter_and_diffs_against_inventory() -> None:
    tasks = _load_role_tasks("decommission_stale_workers.yml")
    fact = next(t for t in tasks if t["name"].startswith("Determine worker ids"))
    expr = fact["ansible.builtin.set_fact"]["hindsight_docker_stale_worker_ids"]
    assert "hindsight_stale_worker_ids(groups['hindsight_group']" in expr
    assert "hindsight_docker_worker_dead_threshold_seconds" in expr

    listing = next(t for t in tasks if t["name"].startswith("List Hindsight workers"))
    list_argv = listing["ansible.builtin.command"]["argv"]
    assert "HINDSIGHT_API_DATABASE_URL" in list_argv
    assert not any("hindsight_docker_db_url" in a for a in list_argv)
    assert listing["environment"] == {"HINDSIGHT_API_DATABASE_URL": "{{ hindsight_docker_db_url }}"}
    # S4: output can carry the DSN on a connection error, so it's withheld —
    # a follow-up rc-only assert keeps a real failure visible regardless.
    assert listing["no_log"] is True
    assert listing["failed_when"] is False

    decommission = next(t for t in tasks if t["name"].startswith("Decommission worker ids"))
    argv = decommission["ansible.builtin.command"]["argv"]
    # Singular, per-id decommission-worker only — decommission-workers
    # (plural) releases every worker unconditionally, including live ones.
    assert argv[-3:] == ["decommission-worker", "{{ item }}", "--yes"]
    assert "HINDSIGHT_API_DATABASE_URL" in argv
    assert not any("hindsight_docker_db_url" in a for a in argv)
    assert decommission["environment"] == {"HINDSIGHT_API_DATABASE_URL": "{{ hindsight_docker_db_url }}"}
    assert decommission["loop"] == "{{ hindsight_docker_stale_worker_ids | default([]) }}"
    assert decommission["no_log"] is True
    assert decommission["failed_when"] is False

    follow_up = next(t for t in tasks if t["name"].startswith("Fail loudly if any decommission"))
    assert follow_up["ansible.builtin.assert"]["that"] == ["item.rc == 0"]
    assert follow_up["loop"] == "{{ hindsight_docker_decommission_result.results | default([]) }}"


def test_stale_worker_filter_keeps_present_and_live_absent_decommissions_only_dead_absent() -> None:
    # B1/S6: the actual decommission logic, evaluated against sample
    # worker-status text and a sample inventory group — not just structure.
    sample = (
        "Processing tasks across 3 worker(s):\n\n"
        "Worker: present-and-stale (1 task(s))\n"
        "  aaaaaaaa  reflect              bank=demo  running=5:00:00  last_update=5:00:00 ago\n\n"
        "Worker: absent-but-fresh (1 task(s))\n"
        "  bbbbbbbb  retain               bank=demo  running=0:05:00  last_update=0:05:00 ago\n\n"
        "Worker: absent-and-dead (1 task(s))\n"
        "  cccccccc  consolidation        bank=demo  running=2:10:00  last_update=2:10:00 ago\n\n"
    )
    inventory_group = ["present-and-stale"]
    threshold_seconds = 4800  # 80 minutes

    stale = hindsight_stale_worker_ids(sample, inventory_group, threshold_seconds)

    assert stale == ["absent-and-dead"]  # only the absent AND dead id


def test_stale_worker_filter_parses_multi_day_last_update_ages() -> None:
    # S-new-1: str(timedelta) embeds its own ", " at >=1 day ("1 day, ..."
    # singular, "N days, ..." plural), so a \S+ token regex silently misses
    # every task 24h or older, reading it as fresh (age 0) instead of dead.
    # Covers under-1-day, both day forms, and microseconds-present.
    sample = (
        "Processing tasks across 3 worker(s):\n\n"
        "Worker: dead-under-a-day (1 task(s))\n"
        "  aaaaaaaa  retain               bank=demo  running=5:00:00  last_update=5:00:00 ago\n\n"
        "Worker: dead-one-day-singular (1 task(s))\n"
        "  bbbbbbbb  retain               bank=demo  running=1 day, 0:00:00"
        "  last_update=1 day, 0:00:00 ago\n\n"
        "Worker: dead-multi-day-with-micros (1 task(s))\n"
        "  cccccccc  retain               bank=demo  running=3 days, 1:02:03.456789"
        "  last_update=3 days, 1:02:03.456789 ago\n\n"
    )
    stale = hindsight_stale_worker_ids(sample, [], dead_threshold_seconds=4800)
    assert set(stale) == {"dead-under-a-day", "dead-one-day-singular", "dead-multi-day-with-micros"}


def test_stale_worker_filter_renders_from_the_real_task_expression() -> None:
    # Literal render of the YAML task's own Jinja expression (not a re-typed
    # copy), with the filter registered exactly as Ansible would load it.
    import jinja2

    tasks = _load_role_tasks("decommission_stale_workers.yml")
    fact = next(t for t in tasks if t["name"].startswith("Determine worker ids"))
    expr = fact["ansible.builtin.set_fact"]["hindsight_docker_stale_worker_ids"]

    env = jinja2.Environment()
    env.filters["hindsight_stale_worker_ids"] = hindsight_stale_worker_ids
    template = env.from_string(expr)

    rendered = template.render(
        hindsight_docker_worker_status={
            "stdout": (
                "Processing tasks across 1 worker(s):\n\n"
                "Worker: absent-and-dead (1 task(s))\n"
                "  cccccccc  consolidation        bank=demo  running=2:10:00  last_update=2:10:00 ago\n\n"
            )
        },
        groups={"hindsight_group": []},
        hindsight_docker_worker_dead_threshold_seconds=4800,
    )
    assert rendered == "['absent-and-dead']"


def test_site_yml_imports_the_hindsight_play() -> None:
    # The play itself was split into playbooks/hindsight.yml to keep site.yml
    # under its token budget (.token-limits.yaml) — same pattern already
    # used there for agents.yml, llm-serving.yml, and phoenix.yml.
    site = yaml.safe_load(SITE_YML.read_text())
    hindsight_entry = next(p for p in site if p.get("name") == "Configure Hindsight agent-memory service")
    assert hindsight_entry["import_playbook"] == "hindsight.yml"


def test_site_yml_wires_the_migration_gate_before_the_isolated_block() -> None:
    hindsight_plays = yaml.safe_load(HINDSIGHT_PLAYBOOK.read_text())
    rolling_play = hindsight_plays[0]
    assert rolling_play["max_fail_percentage"] == 0
    assert rolling_play["serial"] == 1
    pre_task_names = [t["name"] for t in rolling_play["pre_tasks"]]
    gates_task = next(t for t in rolling_play["pre_tasks"] if t["name"].startswith("Run the Hindsight"))
    assert gates_task["ansible.builtin.import_tasks"] == "tasks/hindsight_lifecycle_gates.yml"
    # Precedes the role's normal (rescue-wrapped) entry, after the pool gate.
    pool_gate_index = pre_task_names.index("Gate on pool-member reachability")
    gates_index = pre_task_names.index(gates_task["name"])
    assert pool_gate_index < gates_index


def test_lifecycle_gates_file_uses_include_role_so_role_defaults_resolve() -> None:
    # include_role (not import_tasks on the role's raw task path) is
    # load-bearing: it is what makes the role's own defaults/main.yml (image
    # tag, db_url, the pre-migration backup dir) resolve before this fires
    # in pre_tasks, ahead of `tasks:` where the role is otherwise entered.
    gates = yaml.safe_load((REPO_ROOT / "playbooks/tasks/hindsight_lifecycle_gates.yml").read_text())
    names = [t["name"] for t in gates]
    assert "Run the Hindsight database migration once, before any replica redeploys" in names
    assert "Decommission Hindsight worker ids no longer present in inventory" not in names
    migration = next(t for t in gates if t["name"].startswith("Run the Hindsight database migration"))
    assert migration["ansible.builtin.include_role"] == {
        "name": "hindsight_docker",
        "tasks_from": "run_db_migration.yml",
    }


def test_decommission_runs_from_its_own_final_play_not_serial_one() -> None:
    # B1: a per-batch pre_tasks placement (serial: 1) let the decommission
    # gate race a same-batch inventory rename. It now lives in a SEPARATE,
    # non-serial play that runs after every batch of the rolling play above
    # has redeployed and passed its health check.
    hindsight_plays = yaml.safe_load(HINDSIGHT_PLAYBOOK.read_text())
    assert len(hindsight_plays) == 2
    decommission_play = hindsight_plays[1]
    assert decommission_play["hosts"] == "hindsight_group"
    assert "serial" not in decommission_play
    task_names = [t["name"] for t in decommission_play["tasks"]]
    assert "Decommission Hindsight worker ids no longer present in inventory" in task_names
    decommission_task = next(
        t for t in decommission_play["tasks"] if t["name"].startswith("Decommission Hindsight worker ids")
    )
    assert decommission_task["ansible.builtin.include_role"] == {
        "name": "hindsight_docker",
        "tasks_from": "decommission_stale_workers.yml",
    }


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
