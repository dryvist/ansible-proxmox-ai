"""Check the built-in ClickHouse Prometheus endpoint config and mount."""

from pathlib import Path
import xml.etree.ElementTree as ET

import jinja2


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/clickhouse_docker"
CONFIG = ROLE / "templates/config.d/prometheus.xml.j2"


def test_prometheus_exports_read_only_metrics_events_and_async_metrics():
    rendered = jinja2.Template(CONFIG.read_text()).render(
        clickhouse_docker_prometheus_port=9363
    )
    prometheus = ET.fromstring(rendered).find("prometheus")

    assert prometheus.findtext("port") == "9363"
    assert prometheus.findtext("endpoint") == "/metrics"
    assert prometheus.findtext("metrics") == "true"
    assert prometheus.findtext("asynchronous_metrics") == "true"
    assert prometheus.findtext("events") == "true"


def test_prometheus_config_is_deployed_and_mounted_read_only():
    compose = (ROLE / "templates/docker-compose.yml.j2").read_text()
    tasks = (ROLE / "tasks/main.yml").read_text()

    assert "src: config.d/prometheus.xml.j2" in tasks
    assert "prometheus.xml:/etc/clickhouse-server/config.d/prometheus.xml:ro" in compose
    assert '"{{ clickhouse_docker_prometheus_port }}:{{ clickhouse_docker_prometheus_port }}"' in compose
