"""Keep dedicated ClickHouse wiring aligned across inventory and playbooks."""

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_site_converges_clickhouse_before_langfuse_and_prefetches_its_domain():
    site_plays = yaml.safe_load((ROOT / "playbooks/site.yml").read_text())
    orchestration_imports = [
        play for play in site_plays
        if play.get("import_playbook") == "ai-orchestration.yml"
    ]
    plays = yaml.safe_load((ROOT / "playbooks/ai-orchestration.yml").read_text())
    clickhouse_play = next(play for play in plays if play.get("hosts") == "clickhouse_group")
    langfuse_index = next(
        index for index, play in enumerate(plays)
        if play.get("hosts") == "langfuse_group"
    )
    clickhouse_index = plays.index(clickhouse_play)
    role_task = next(
        task for task in clickhouse_play["tasks"][0]["block"]
        if task.get("name") == "Include clickhouse_docker role"
    )
    secret_play = next(
        play for play in site_plays
        if "clickhouse_group" in play.get("hosts", "")
        and play.get("tags") == "always"
    )
    published = next(
        task["ansible.builtin.set_fact"] for task in secret_play["tasks"]
        if "ansible.builtin.set_fact" in task
        and "bao_clickhouse_secrets" in task["ansible.builtin.set_fact"]
    )

    assert clickhouse_play["become"] is True
    assert "clickhouse_docker" in clickhouse_play["tags"]
    assert role_task["ansible.builtin.include_role"]["name"] == "clickhouse_docker"
    assert len(orchestration_imports) == 1
    assert clickhouse_index < langfuse_index
    assert published["bao_clickhouse_secrets"] == (
        "{{ openbao_secrets_by_domain['clickhouse'] | default({}) }}"
    )


def test_clickhouse_inventory_contract_uses_published_ports_and_guest_tag():
    fixture = json.loads((ROOT / "tests/inventory_load/tofu_inventory.json").read_text())
    guest = fixture["containers"]["clickhouse"]
    group_vars = yaml.safe_load((ROOT / "inventory/group_vars/clickhouse_group.yml").read_text())
    defaults = yaml.safe_load((ROOT / "roles/clickhouse_docker/defaults/main.yml").read_text())

    assert "clickhouse" in guest["tags"]
    assert "managed-secrets" not in guest["tags"]
    assert fixture["constants"]["service_ports"]["clickhouse_http"] == "8123"
    assert fixture["constants"]["service_ports"]["clickhouse_native"] == "9000"
    assert group_vars["clickhouse_docker_ch_data_dir"] == "/var/lib/clickhouse"
    assert group_vars["clickhouse_docker_uid"] == "101"
    assert group_vars["clickhouse_docker_timezone"] == "UTC"
    assert group_vars["clickhouse_docker_database"] == "default"
    assert group_vars["clickhouse_docker_app_user"] == "langfuse"
    assert defaults["clickhouse_docker_version"] == "25.12"
    assert defaults["clickhouse_docker_prometheus_port"].endswith("default(9363) }}")
    assert "clickhouse_docker_app_grants" not in defaults


def test_external_langfuse_password_projects_the_clickhouse_openbao_field():
    group_vars = yaml.safe_load((ROOT / "inventory/group_vars/langfuse_group.yml").read_text())

    assert group_vars["langfuse_docker_clickhouse_endpoint"] == "embedded"
    external_password = group_vars["langfuse_docker_clickhouse_external_password"]
    assert "bao_clickhouse_secrets" in external_password
    assert "APP_PASSWORD" in external_password
