"""Render the role with documented inventory fixtures and native Ansible filters."""

from pathlib import Path

import jinja2
import yaml
from ansible.plugins.filter.core import mandatory, to_bool


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/clickhouse_docker"


def defaults():
    result = {}
    for path in sorted((ROLE / "defaults/main").glob("*.yml")):
        result.update(yaml.safe_load(path.read_text()))
    return result


def environment():
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters.update(mandatory=mandatory, bool=to_bool)
    return env


def inventory():
    return {"localhost": {"tofu_data": {
        "domain": "example.test",
        "containers": {"grafana": {"hostname": "metrics"}},
        "constants": {"service_ports": {"otel_traces_http": 14318, "victoriametrics": 18428}},
    }}}


def compose(enabled=True):
    variables = defaults()
    # Resolve only values used by Compose, leaving credential lookups untouched.
    variables.update(
        hostvars=inventory(),
        clickhouse_docker_otelcol_enabled=enabled,
        clickhouse_docker_app_password="fixture-not-a-secret",
        clickhouse_docker_http_port=18123,
        clickhouse_docker_native_port=19000,
        clickhouse_docker_prometheus_port=19363,
        clickhouse_docker_host="192.0.2.10",
    )
    env = environment()
    for _ in range(3):
        variables.update({key: env.from_string(value).render(variables)
                          for key, value in variables.items()
                          if isinstance(value, str) and "{{" in value
                          and "bao_" not in value})
    template = (ROLE / "templates/docker-compose.yml.j2").read_text()
    return yaml.safe_load(env.from_string(template).render(variables))
