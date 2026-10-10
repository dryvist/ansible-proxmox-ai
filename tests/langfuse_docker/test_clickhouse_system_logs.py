"""Pin the ClickHouse config.d override that the langfuse_docker role renders."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2
import yaml

ROLE_DIR = Path(__file__).resolve().parents[2] / "roles/langfuse_docker"
DEFAULTS_FILE = ROLE_DIR / "defaults/main.yml"
OVERRIDE_TEMPLATE = ROLE_DIR / "templates/clickhouse-system-logs.xml.j2"
COMPOSE_TEMPLATE = ROLE_DIR / "templates/docker-compose.yml.j2"
OVERRIDE_MOUNT = (
    "{{ langfuse_docker_clickhouse_config_dir }}/system_logs.xml"
    ":/etc/clickhouse-server/config.d/system_logs.xml:ro"
)


def _render_override() -> ET.Element:
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text(encoding="utf-8"))
    rendered = jinja2.Template(
        OVERRIDE_TEMPLATE.read_text(encoding="utf-8")
    ).render(**defaults)
    return ET.fromstring(rendered)


def test_logger_and_text_log_level_is_information():
    root = _render_override()

    assert root.findtext("logger/level") == "information"
    assert root.findtext("text_log/level") == "information"


def test_system_logs_expire_after_30_days():
    root = _render_override()

    for table in ("text_log", "query_log", "trace_log", "part_log",
                  "metric_log", "asynchronous_metric_log"):
        assert root.findtext(f"{table}/ttl") == (
            "event_date + INTERVAL 30 DAY DELETE"
        ), table


def test_span_log_collection_is_removed_without_redefining_the_table():
    span_log = _render_override().find("opentelemetry_span_log")

    assert span_log is not None
    assert span_log.get("remove") == "1"
    assert len(list(span_log)) == 0


def test_compose_mounts_the_override_into_config_d_read_only():
    assert OVERRIDE_MOUNT in COMPOSE_TEMPLATE.read_text(encoding="utf-8")
