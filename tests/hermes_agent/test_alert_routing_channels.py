"""Self-check for the Hermes four-way alert-channel contract, and the
regression (hermes_all aliasing the firehose) that caused the collapse.

Split from test_alert_routing.py to stay under the token budget — see
_alert_routing_shared.py for the shared resolve/deliver-target helpers,
test_alert_routing_jobs.py for the per-job direct-cron routing contract, and
test_alert_routing_helper.py for the runtime cron-route helper this leaves
behind.

Runs bare (`python3 tests/hermes_agent/test_alert_routing_channels.py`) or
under pytest.
"""

from __future__ import annotations

from _alert_routing_shared import (
    CONFIGURED,
    KANBAN,
    MAIN_TASKS,
    SPLUNK_STATUS,
    TRIAGE,
    ZAMMAD_CLOSE,
    _deliver_targets,
    _resolve,
)


# --- the four-way contract ---------------------------------------------------

def test_splunk_digests_go_to_the_splunk_channel() -> None:
    ctx = _resolve(CONFIGURED)
    assert _deliver_targets(SPLUNK_STATUS, ctx, MAIN_TASKS) == "slack:C_SPLUNK"
    assert _deliver_targets(TRIAGE, ctx, MAIN_TASKS) == "slack:C_SPLUNK"


def test_splunk_digests_do_not_also_land_in_the_work_channel() -> None:
    """The negative case. #hermes-all was the log of record and that is exactly
    what made it unreadable — every emitter appended a leg to it."""
    ctx = _resolve(CONFIGURED)
    for pattern in (SPLUNK_STATUS, TRIAGE):
        assert "C_ALL" not in _deliver_targets(pattern, ctx, MAIN_TASKS)


def test_the_board_digest_is_work_and_goes_to_the_work_channel() -> None:
    ctx = _resolve(CONFIGURED)
    assert _deliver_targets(KANBAN, ctx, MAIN_TASKS) == "slack:C_ALL"


def test_board_worker_failures_go_to_the_issues_channel() -> None:
    """Turn-budget exhaustion and judge timeouts are Hermes broken, not board
    activity. The script routes that section itself; this pins its destination."""
    ctx = _resolve(CONFIGURED)
    assert ctx["hermes_agent_kanban_digest_issues_channel"] == "C_ISSUES"


def test_the_fabric_watchdog_alerts_to_the_issues_channel() -> None:
    """These probe Hermes' OWN fabric, so a flap is breakage. 86 messages in the
    audit window, several of them recoveries with no matching DOWN."""
    ctx = _resolve(CONFIGURED)
    assert ctx["fabric_watchdog_alert_channel"] == "C_ISSUES"


def test_zammad_closures_stay_an_audit_record_in_the_work_channel() -> None:
    ctx = _resolve(CONFIGURED)
    assert _deliver_targets(ZAMMAD_CLOSE, ctx, MAIN_TASKS) == "slack:C_ALL"


# --- the regression that caused the collapse ---------------------------------

def test_hermes_all_is_not_an_alias_of_the_firehose() -> None:
    """The root cause. While hermes_all was defined as
    ``{{ hermes_agent_slack_firehose_channel }}``, no configuration could make
    the work channel distinct from the firehose — every tier resolved to one id.
    """
    # Asserted as behaviour, not as a source-text match. The env-var name is
    # now built from the agent identity so a second agent reads its own
    # channel, which means there is no literal in defaults to grep for — and
    # the property that actually matters was never the spelling. These two
    # cases pin it from both sides: its own channel wins when set, and the
    # firehose is only ever reached as a fallback.
    ctx = _resolve(CONFIGURED)
    assert ctx["hermes_agent_slack_hermes_all_channel"] == "C_ALL" != "C_FIRE"

    unset = _resolve({k: v for k, v in CONFIGURED.items() if k != "SLACK_HERMES_ALL_CHANNEL"})
    assert unset["hermes_agent_slack_hermes_all_channel"] == "C_FIRE"

    # And the identity really does select the name, so the second agent cannot
    # silently inherit this one's work channel.
    assert _resolve({**CONFIGURED, "SLACK_DONNA_ALL_CHANNEL": "C_DONNA"})[
        "hermes_agent_slack_hermes_all_channel"
    ] == "C_ALL"


def test_the_four_channels_resolve_to_four_distinct_surfaces() -> None:
    ctx = _resolve(CONFIGURED)
    live = {
        _deliver_targets(SPLUNK_STATUS, ctx, MAIN_TASKS),
        _deliver_targets(KANBAN, ctx, MAIN_TASKS),
        "slack:" + ctx["hermes_agent_slack_issues_channel"],
        "slack:" + ctx["hermes_agent_slack_noise_channel"],
    }
    assert len(live) == 4, f"emitters collapsed onto fewer channels: {live}"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
