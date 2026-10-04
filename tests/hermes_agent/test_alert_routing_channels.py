"""Self-check for the Hermes one-channel contract: every emitter posts to the
agent's home channel.

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


def test_every_slack_channel_var_resolves_to_the_home_channel() -> None:
    ctx = _resolve(CONFIGURED)
    names = [n for n in DEFAULTS
             if n.startswith("hermes_agent_slack_") and n.endswith("_channel")]
    assert names
    for name in names:
        assert ctx[name] == "C_HOME", name
    assert ctx["hermes_agent_digest_slack_channel"] == "C_HOME"


def test_digests_and_board_post_to_the_home_channel() -> None:
    ctx = _resolve(CONFIGURED)
    for pattern in (SPLUNK_STATUS, TRIAGE, KANBAN):
        assert _deliver_targets(pattern, ctx, MAIN_TASKS) == "slack:C_HOME"


def test_board_worker_failures_post_to_the_home_channel() -> None:
    ctx = _resolve(CONFIGURED)
    assert ctx["hermes_agent_kanban_digest_issues_channel"] == "C_HOME"


def test_the_retired_per_purpose_env_vars_are_ignored() -> None:
    ctx = _resolve({**CONFIGURED,
                    "SLACK_HERMES_ALL_CHANNEL": "C_ALL",
                    "SLACK_HERMES_ISSUES_CHANNEL": "C_ISSUES",
                    "SLACK_HERMES_SPLUNK_CHANNEL": "C_SPLUNK",
                    "SLACK_HERMES_NOISE_CHANNEL": "C_NOISE"})
    for pattern in (SPLUNK_STATUS, TRIAGE, KANBAN):
        assert _deliver_targets(pattern, ctx, MAIN_TASKS) == "slack:C_HOME"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
