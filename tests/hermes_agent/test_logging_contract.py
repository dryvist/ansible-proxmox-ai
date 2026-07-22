from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = (REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text()
RSYSLOG = (REPO_ROOT / "roles/hermes_agent/templates/hermes-agent-rsyslog.conf.j2").read_text()


def test_debug_logging_remains_enabled() -> None:
    assert "hermes_agent_log_level: DEBUG" in DEFAULTS


def test_rsyslog_routes_unit_children_and_file_logs() -> None:
    assert "$!_SYSTEMD_UNIT startswith 'hermes-'" in RSYSLOG
    assert 'File="{{ hermes_agent_log_directory }}/*.log"' in RSYSLOG
    assert 'Ruleset="hermes_agent"' in RSYSLOG
