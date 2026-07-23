from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = (REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text()
RSYSLOG = (REPO_ROOT / "roles/hermes_agent/templates/hermes-agent-rsyslog.conf.j2").read_text()
TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/main.yml").read_text()


def test_debug_logging_remains_enabled() -> None:
    assert "hermes_agent_log_level: DEBUG" in DEFAULTS


def test_rsyslog_routes_unit_children_and_file_logs() -> None:
    assert "$!_SYSTEMD_UNIT startswith 'hermes-'" in RSYSLOG
    assert 'File="{{ hermes_agent_log_directory }}/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/logs/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/kanban/boards/*/logs/*.log"' in RSYSLOG
    assert 'File="{{ hermes_agent_hermes_home }}/profiles/*/logs/*.log"' in RSYSLOG
    assert 'Ruleset="hermes_agent"' in RSYSLOG
    assert 'freshStartTail="off"' in RSYSLOG


def test_prompt_safe_context_metrics_are_enabled_without_verbose_payload_logging() -> None:
    assert "Enable prompt-safe Hermes request size metrics at DEBUG" in TASKS
    assert "Enable prompt-safe Hermes token usage metrics at DEBUG" in TASKS
    assert "verbose_logging: true" not in DEFAULTS
