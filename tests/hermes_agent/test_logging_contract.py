from pathlib import Path
import re

import pytest
import yaml
from jinja2 import Environment

from _role_files import role_defaults, role_defaults_text, role_tasks_text


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = role_defaults_text(REPO_ROOT / "roles" / "hermes_agent")
RSYSLOG = (REPO_ROOT / "roles/hermes_agent/templates/hermes-agent-rsyslog.conf.j2").read_text()
TASKS = role_tasks_text(REPO_ROOT / "roles" / "hermes_agent")
DASHBOARD_LOGGING = (
    REPO_ROOT / "roles/hermes_agent/templates/hermes-dashboard-logging.conf.j2"
).read_text()
DASHBOARD_UNIT_TASKS = (
    REPO_ROOT / "roles/hermes_agent/tasks/gateway_and_dashboard_units.yml"
).read_text()


def test_info_is_the_default_log_level_and_stays_overridable() -> None:
    assert "hermes_agent_log_level: INFO" in DEFAULTS
    assert "hermes_agent_log_level: DEBUG" not in DEFAULTS
    for template in ("config.yaml.j2", "config-profile.yaml.j2", "config-public-gateway.yaml.j2"):
        text = (REPO_ROOT / "roles/hermes_agent/templates" / template).read_text()
        assert "level: {{ hermes_agent_log_level }}" in text


def test_rsyslog_routes_unit_children_and_file_logs() -> None:
    assert "$!_SYSTEMD_UNIT startswith 'hermes-'" in RSYSLOG
    assert "($programname == 'systemd' and $msg contains 'hermes-')" in RSYSLOG
    assert 'File="{{ hermes_agent_log_directory }}/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/logs/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/boards/*/logs/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/profiles/*/logs/*.log"' in RSYSLOG
    assert 'Ruleset="hermes_agent"' in RSYSLOG
    assert 'freshStartTail="off"' in RSYSLOG


@pytest.mark.parametrize(("identity_tags", "roster_identity"), [
    (["hermes-agent"], None),
    (["hermes-agent", "hermes-private"], None),
    (["hermes-agent", "hermes-donna"], None),
    (["hermes-agent", "hermes-pepper"], None),
    ([], "roster-fixture"),
])
def test_rsyslog_paths_follow_inventory_identity_for_every_hermes_host(
    identity_tags: list[str], roster_identity: str | None,
) -> None:
    inventory_tasks = yaml.safe_load(
        (REPO_ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml").read_text()
    )
    add_host = next(task for task in inventory_tasks if "ansible.builtin.add_host" in task)
    identity_expression = add_host["ansible.builtin.add_host"]["hermes_agent_identity"]
    roster_map = {"fixture-hermes": roster_identity} if roster_identity else {}
    identity = Environment(autoescape=False).from_string(identity_expression).render(
        item={"key": "fixture-hermes", "value": {"tags": identity_tags}},
        _ai_agent_by_container=roster_map,
    )
    if roster_identity:
        assert identity == roster_identity

    group_env = Environment(autoescape=False)
    group_env.filters.update(
        bool=bool,
        intersect=lambda values, selected: [value for value in values if value in selected],
        regex_replace=lambda value, pattern, replacement: re.sub(pattern, replacement, value),
    )
    groups = yaml.safe_load(group_env.from_string(
        (REPO_ROOT / "inventory/load_tofu/add_lxc_host_groups.yml.j2").read_text()
    ).render(
        item={"key": "fixture-hermes", "value": {"tags": identity_tags}},
        _ai_agent_by_container=roster_map,
        _llm_cpu_pool_tags=[],
    ))
    assert "hermes_agent_group" in groups

    defaults = role_defaults(REPO_ROOT / "roles/hermes_agent")
    context = {"hermes_agent_identity": identity, "ai_agents": {identity: {}}}
    env = Environment(autoescape=False)
    for key in ("hermes_agent_id", "hermes_agent_user", "hermes_agent_home",
                "hermes_agent_hermes_home", "hermes_agent_log_directory"):
        context[key] = env.from_string(str(defaults[key])).render(**context)

    env.filters["comment"] = lambda value: f"# {value}"
    rendered = env.from_string(RSYSLOG).render(
        ansible_managed="managed by test",
        hermes_agent_syslog_host="syslog.example.invalid",
        hermes_agent_syslog_port=6514,
        hermes_agent_syslog_queue_max_disk_space="256m",
        hermes_agent_hermes_home=context["hermes_agent_hermes_home"],
        hermes_agent_log_directory=context["hermes_agent_log_directory"],
    )
    home = context["hermes_agent_hermes_home"]
    assert home == f"/var/lib/{identity}/.hermes"
    for path in (
        f"{home}/logs/*.log",
        f"{home}/kanban/logs/*.log",
        f"{home}/kanban/boards/*/logs/*.log",
        f"{home}/profiles/*/logs/*.log",
    ):
        assert f'File="{path}"' in rendered


def test_rsyslog_route_is_enabled_and_deployed_by_the_logging_tag() -> None:
    assert "hermes_agent_syslog_route_enabled: true" in DEFAULTS
    play = yaml.safe_load((REPO_ROOT / "playbooks/agents.yml").read_text())[0]
    assert play["hosts"] == "hermes_agent_group"
    runtime = next(task for task in play["tasks"][0]["block"]
                   if task.get("name") == "Include hermes_agent role")
    assert runtime["ansible.builtin.import_role"]["name"] == "hermes_agent"
    assert "hermes_agent_pepper_runtime_selected" in runtime["when"]

    role_tasks = yaml.safe_load((REPO_ROOT / "roles/hermes_agent/tasks/main.yml").read_text())
    logging = next(task for task in role_tasks
                   if task.get("ansible.builtin.import_tasks") == "syslog_route.yml")
    assert set(logging["tags"]) == {"hermes_agent_logging", "hermes_units_gateways"}
    assert logging["when"] == "hermes_agent_syslog_route_enabled | bool"
    finalize_entry = next(task for task in role_tasks
                          if task.get("ansible.builtin.import_tasks") == "finalize.yml")
    assert finalize_entry["tags"] == "hermes_units_gateways"
    finalize = yaml.safe_load((REPO_ROOT / "roles/hermes_agent/tasks/finalize.yml").read_text())
    assert all(task.get("ansible.builtin.import_tasks") != "syslog_route.yml" for task in finalize)

    syslog_tasks = yaml.safe_load((REPO_ROOT / "roles/hermes_agent/tasks/syslog_route.yml").read_text())
    deploy = next(task for task in syslog_tasks
                  if task.get("name") == "Deploy the hermes-agent rsyslog forwarding ruleset")
    assert deploy["ansible.builtin.template"]["src"] == "hermes-agent-rsyslog.conf.j2"
    service = next(task for task in syslog_tasks if task.get("name") == "Enable and start rsyslog")
    assert service["ansible.builtin.systemd"] == {
        "name": "rsyslog", "state": "started", "enabled": True,
    }


def test_dashboard_journal_uses_the_existing_hermes_rsyslog_route() -> None:
    assert "hermes-dashboard.service.d/10-logging.conf" in DASHBOARD_UNIT_TASKS
    assert "src: hermes-dashboard-logging.conf.j2" in DASHBOARD_UNIT_TASKS
    assert "StandardOutput=journal" in DASHBOARD_LOGGING
    assert "StandardError=journal" in DASHBOARD_LOGGING
    assert "SyslogIdentifier=hermes" in DASHBOARD_LOGGING
    assert "$programname startswith 'hermes'" in RSYSLOG


def test_verbose_payload_logging_is_not_forced_on() -> None:
    # Reverted: these two patches forced request/token-usage debug logging on
    # unconditionally, regardless of the configured verbose_logging setting.
    assert "Enable prompt-safe Hermes request size metrics at DEBUG" not in TASKS
    assert "Enable prompt-safe Hermes token usage metrics at DEBUG" not in TASKS
    assert "verbose_logging: true" not in DEFAULTS
