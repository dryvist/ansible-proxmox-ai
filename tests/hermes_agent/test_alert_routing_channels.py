"""Self-check for the Hermes routing contract: work, failures and Splunk
findings use the channels configured for their readers.

See _alert_routing_shared.py for the shared resolve/deliver-target helpers,
test_alert_routing_jobs.py for the per-job direct-cron routing contract, and
test_alert_routing_helper.py for the runtime cron-route helper.

Runs bare (`python3 tests/hermes_agent/test_alert_routing_channels.py`) or
under pytest.
"""

from __future__ import annotations

from _alert_routing_shared import (
    CONFIGURED,
    DEFAULTS,
    KANBAN,
    MAIN_TASKS,
    SPLUNK_STATUS,
    TRIAGE,
    _deliver_targets,
    _resolve,
)


def test_failure_and_splunk_channels_resolve_from_identity_environment() -> None:
    ctx = _resolve(CONFIGURED)
    assert ctx["hermes_agent_slack_hermes_all_channel"] == "C_HOME"
    assert ctx["hermes_agent_slack_issues_channel"] == "C_ALERTS"
    assert ctx["hermes_agent_slack_splunk_channel"] == "C_SPLUNK"
    assert ctx["hermes_agent_slack_noise_channel"] == "C_HOME"
    assert ctx["hermes_agent_digest_slack_channel"] == "C_HOME"


def test_splunk_findings_and_work_reports_keep_their_destinations() -> None:
    ctx = _resolve(CONFIGURED)
    assert _deliver_targets(SPLUNK_STATUS, ctx, MAIN_TASKS) == "slack:C_SPLUNK"
    assert _deliver_targets(TRIAGE, ctx, MAIN_TASKS) == "slack:C_SPLUNK"
    assert _deliver_targets(KANBAN, ctx, MAIN_TASKS) == "slack:C_HOME"


def test_board_worker_failures_use_the_configured_alert_channel() -> None:
    ctx = _resolve(CONFIGURED)
    assert ctx["hermes_agent_kanban_digest_issues_channel"] == "C_ALERTS"


def test_failure_and_splunk_routes_honor_identity_environment() -> None:
    ctx = _resolve({**CONFIGURED,
                    "SLACK_HERMES_ALL_CHANNEL": "C_ALL",
                    "SLACK_HERMES_ISSUES_CHANNEL": "C_ISSUES",
                    "SLACK_HERMES_SPLUNK_CHANNEL": "C_SPLUNK",
                    "SLACK_HERMES_NOISE_CHANNEL": "C_NOISE"})
    assert _deliver_targets(SPLUNK_STATUS, ctx, MAIN_TASKS) == "slack:C_SPLUNK"
    assert _deliver_targets(TRIAGE, ctx, MAIN_TASKS) == "slack:C_SPLUNK"
    assert _deliver_targets(KANBAN, ctx, MAIN_TASKS) == "slack:C_HOME"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
