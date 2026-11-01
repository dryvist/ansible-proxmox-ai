from pathlib import Path
from _role_files import role_defaults_text, role_tasks_text


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


def test_debug_logging_remains_enabled() -> None:
    assert "hermes_agent_log_level: DEBUG" in DEFAULTS


def test_rsyslog_routes_unit_children_and_file_logs() -> None:
    assert "$!_SYSTEMD_UNIT startswith 'hermes-'" in RSYSLOG
    assert "($programname == 'systemd' and $msg contains 'hermes-')" in RSYSLOG
    assert 'File="{{ hermes_agent_log_directory }}/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/logs/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/boards/*/logs/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/profiles/*/logs/*.log"' in RSYSLOG
    assert 'Ruleset="hermes_agent"' in RSYSLOG
    assert 'freshStartTail="off"' in RSYSLOG


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
