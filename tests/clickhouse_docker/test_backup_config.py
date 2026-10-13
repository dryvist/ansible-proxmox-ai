"""Check ClickHouse backup and system-log configuration for both roles."""

import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
CLICKHOUSE_ROLE = ROOT / "roles/clickhouse_docker"
LANGFUSE_ROLE = ROOT / "roles/langfuse_docker"
SYSTEM_LOG_TABLES = (
    "text_log",
    "query_log",
    "trace_log",
    "part_log",
    "metric_log",
    "asynchronous_metric_log",
)


def _defaults(role: Path) -> dict:
    files = sorted((role / "defaults/main").glob("*.yml")) or [role / "defaults/main.yml"]
    merged: dict = {}
    for path in files:
        merged.update(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return merged


def _render(role: Path, template: str, defaults: dict) -> ET.Element:
    rendered = jinja2.Template(
        (role / "templates" / template).read_text(encoding="utf-8")
    ).render(**defaults)
    return ET.fromstring(rendered)


def test_both_roles_allow_backup_files_under_clickhouse_data():
    for role in (CLICKHOUSE_ROLE, LANGFUSE_ROLE):
        root = _render(role, "config.d/backups.xml.j2", _defaults(role))

        assert root.findtext("backups/allowed_path") == ("/var/lib/clickhouse/backups/")


def test_dedicated_clickhouse_system_logs_use_information_and_30_day_ttl():
    defaults = _defaults(CLICKHOUSE_ROLE)
    root = _render(CLICKHOUSE_ROLE, "config.d/system-logs.xml.j2", defaults)

    assert tuple(defaults["clickhouse_docker_system_log_ttl_tables"]) == (
        SYSTEM_LOG_TABLES
    )
    assert root.findtext("logger/level") == "information"
    assert root.findtext("text_log/level") == "information"
    for table in SYSTEM_LOG_TABLES:
        assert root.findtext(f"{table}/ttl") == ("event_date + INTERVAL 30 DAY DELETE")


def test_database_defaults_to_default_and_grants_follow_the_variable():
    defaults = _defaults(CLICKHOUSE_ROLE)
    grants = defaults["clickhouse_docker_app_grants"]

    assert defaults["clickhouse_docker_database"] == "default"
    assert any("ON {{ clickhouse_docker_database }}.*" in grant for grant in grants)
    assert "CREATE DATABASE" not in (CLICKHOUSE_ROLE / "tasks/main.yml").read_text(
        encoding="utf-8"
    )


def test_backup_dropins_are_rendered_mounted_and_created_on_the_data_mount():
    role_contracts = (
        (
            CLICKHOUSE_ROLE,
            "clickhouse_docker_ch_backups_dir",
            "clickhouse_docker_directories",
            "clickhouse_docker_uid",
            "{{ clickhouse_docker_config_d_dir }}/backups.xml",
            (
                "{{ clickhouse_docker_config_d_dir }}/backups.xml"
                ":/etc/clickhouse-server/config.d/backups.xml:ro"
            ),
            "{{ clickhouse_docker_ch_data_dir }}:/var/lib/clickhouse",
        ),
        (
            LANGFUSE_ROLE,
            "langfuse_docker_clickhouse_backups_dir",
            "langfuse_docker_directories",
            "langfuse_docker_clickhouse_uid",
            "{{ langfuse_docker_clickhouse_config_dir }}/backups.xml",
            (
                "{{ langfuse_docker_clickhouse_config_dir }}/backups.xml"
                ":/etc/clickhouse-server/config.d/backups.xml:ro"
            ),
            "{{ langfuse_docker_clickhouse_data_dir }}:/var/lib/clickhouse",
        ),
    )

    for (
        role,
        backups_var,
        directories_var,
        uid_var,
        destination,
        mount,
        data_mount,
    ) in role_contracts:
        defaults = _defaults(role)
        tasks = (role / "tasks/main.yml").read_text(encoding="utf-8")
        compose = (role / "templates/docker-compose.yml.j2").read_text(encoding="utf-8")
        directory = next(
            entry
            for entry in defaults[directories_var]
            if entry["path"] == f"{{{{ {backups_var} }}}}"
        )

        assert f'loop: "{{{{ {directories_var} }}}}"' in tasks
        assert "mode: \"{{ item.mode | default('0755') }}\"" in tasks
        assert "src: config.d/backups.xml.j2" in tasks
        assert f'dest: "{destination}"' in tasks
        assert mount in compose
        assert data_mount in compose
        assert defaults[uid_var] == "101"
        assert directory["owner"] == f"{{{{ {uid_var} }}}}"
        assert directory["group"] == f"{{{{ {uid_var} }}}}"
        assert directory["mode"] == "0750"
        assert defaults[backups_var].endswith("/backups")


def test_dedicated_clickhouse_mounts_its_system_log_config():
    compose = (CLICKHOUSE_ROLE / "templates/docker-compose.yml.j2").read_text(
        encoding="utf-8"
    )
    tasks = (CLICKHOUSE_ROLE / "tasks/main.yml").read_text(encoding="utf-8")

    assert "src: config.d/system-logs.xml.j2" in tasks
    assert (
        "{{ clickhouse_docker_config_d_dir }}/system_logs.xml"
        ":/etc/clickhouse-server/config.d/system_logs.xml:ro"
    ) in compose
