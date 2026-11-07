"""Collector Compose, inventory, and scoped-deployment contracts."""

import copy
import os
import subprocess

import jinja2
import pytest
import yaml

from _otelcol import ROLE, ROOT, compose, defaults, environment, inventory


def test_one_pinned_non_root_collector_with_readonly_config_and_memory_budget():
    cfg = compose()
    assert defaults()["clickhouse_docker_otelcol_enabled"] is True
    assert set(cfg["services"]) == {"clickhouse", "otel_collector"}
    svc = cfg["services"]["otel_collector"]
    assert svc["image"] == "otel/opentelemetry-collector-contrib:0.164.0"
    assert svc["user"] == "10001:10001"
    assert svc["mem_limit"] == "512m"
    assert svc["command"] == ["--config=/etc/otelcol-contrib/config.yaml",
                              "--feature-gates=connector.spanmetrics.excludeResourceMetrics"]
    assert svc["volumes"] == ["/opt/clickhouse/otelcol-config.yml:/etc/otelcol-contrib/config.yaml:ro"]
    assert svc["ports"] == ["14318:4318"]
    assert svc["environment"] == {"OTEL_HTTP_LISTEN": "0.0.0.0:4318",
                                  "VM_REMOTE_WRITE_URL": "http://metrics.example.test:18428/api/v1/write"}
    assert svc["logging"] == {"driver": "journald", "options": {"tag": "otel_collector"}}
    assert "depends_on" not in svc
    assert "healthcheck" not in svc
    assert "deploy" not in svc  # Compose's single-instance default, no replicas.


def test_disabling_collector_keeps_database_service_identical():
    enabled, disabled = compose(), compose(False)
    assert set(disabled["services"]) == {"clickhouse"}
    assert enabled["services"]["clickhouse"] == disabled["services"]["clickhouse"]
    database = disabled["services"]["clickhouse"]
    assert database["environment"]["CLICKHOUSE_PASSWORD"] == "fixture-not-a-secret"
    assert database["image"] == "clickhouse/clickhouse-server:25.12"
    assert database["ports"] == ["18123:8123", "19000:9000", "19363:19363"]


@pytest.mark.parametrize(("path", "variable"), [
    (("constants", "service_ports", "otel_traces_http"), "clickhouse_docker_otelcol_http_port"),
    (("constants", "service_ports", "victoriametrics"), "clickhouse_docker_otelcol_remote_write_url"),
    (("containers", "grafana", "hostname"), "clickhouse_docker_otelcol_remote_write_url"),
    (("domain",), "clickhouse_docker_otelcol_remote_write_url"),
])
def test_missing_inventory_fails_without_endpoint_fallback(path, variable):
    hostvars = copy.deepcopy(inventory())
    parent = hostvars["localhost"]["tofu_data"]
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    with pytest.raises(jinja2.UndefinedError, match=path[-1]):
        environment().from_string(defaults()[variable]).render(hostvars=hostvars)


def test_config_changes_restart_only_collector_and_compose_removes_disabled_service():
    tasks = yaml.safe_load((ROLE / "tasks/otelcol.yml").read_text())
    render = next(task for task in tasks if "ansible.builtin.template" in task)
    assert render["notify"] == "Restart otel collector"
    assert render["when"] == "clickhouse_docker_otelcol_enabled | bool"
    assert render["ansible.builtin.template"]["mode"] == "0644"
    handlers = yaml.safe_load((ROLE / "handlers/main.yml").read_text())
    handler = next(task for task in handlers if task["name"] == render["notify"])
    module = handler["community.docker.docker_compose_v2"]
    assert module["services"] == ["{{ clickhouse_docker_otelcol_service }}"]
    assert module["dependencies"] is False
    assert module["state"] == "present" and module["recreate"] == "always"
    main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    reconcile = next(task for task in main if task["name"] == "Deploy ClickHouse via docker compose")
    assert reconcile["community.docker.docker_compose_v2"]["remove_orphans"] is True
    assert "'Removing'" in reconcile["changed_when"]
    template = next(task for task in main if task["name"] == "Deploy the pinned ClickHouse compose file")
    assert "notify" not in template  # Native reconciliation handles Compose changes.


def test_collector_tag_reaches_import_render_compose_and_fails_missing_prefetch():
    plays = yaml.safe_load((ROOT / "playbooks/ai-orchestration.yml").read_text())
    play = next(play for play in plays if play.get("hosts") == "clickhouse_group")
    assert "otel_collector" not in play["tags"]  # Do not select all database tasks.
    guard = play["pre_tasks"][0]
    assert guard["tags"] == "otel_collector"
    assert guard["when"] == "'otel_collector' in ansible_run_tags"
    assert guard["ansible.builtin.assert"]["that"] == ["bao_clickhouse_secrets | default({}) | length > 0"]
    block = play["tasks"][0]
    include = next(task for task in block["block"] if "ansible.builtin.include_role" in task)
    assert include["tags"] == "otel_collector"
    fail = block["rescue"][0]
    assert "ansible.builtin.fail" in fail and fail["tags"] == "otel_collector"
    main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    selected = [task for task in main if task.get("tags") == "otel_collector"]
    assert [task["name"] for task in selected] == [
        "Assert every secret rendered into the ClickHouse compose file is present",
        "Configure the trace metrics collector", "Deploy the pinned ClickHouse compose file",
        "Deploy ClickHouse via docker compose",
    ]
    assert selected[0]["ansible.builtin.include_role"]["apply"]["tags"] == "otel_collector"
    assert selected[1]["ansible.builtin.import_tasks"] == "otelcol.yml"


def test_ansible_expands_collector_tag_to_render_and_reconcile(tmp_path):
    play = tmp_path / "tag-selection.yml"
    play.write_text(yaml.safe_dump([{
        "name": "Inspect collector tag selection", "hosts": "localhost", "gather_facts": False,
        "tasks": [{"name": "Expand role", "ansible.builtin.import_role": {"name": str(ROLE)}}],
    }]))
    result = subprocess.run(
        ["ansible-playbook", str(play), "-i", "localhost,", "--list-tasks", "--tags", "otel_collector"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-tmp")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Deploy the trace metrics collector config" in result.stdout
    assert "Deploy the pinned ClickHouse compose file" in result.stdout
    assert "Deploy ClickHouse via docker compose" in result.stdout
    assert "Create ClickHouse data directories" not in result.stdout
    assert "Read the running ClickHouse image tag" not in result.stdout
