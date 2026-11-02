"""Self-checks for inert Hermes Slack routes and channel configuration guards.

Runs bare (`python3 tests/hermes_agent/test_alert_routing_guards.py`) or under pytest.
"""

from __future__ import annotations

import re

import yaml

from _alert_routing_shared import (
    CONFIGURED,
    DEFAULTS,
    DIRECT,
    DIRECT_TASKS,
    KANBAN,
    MAIN_TASKS,
    ROLE,
    SPLUNK_STATUS,
    TRIAGE,
    _deliver_targets,
    _resolve,
)


# --- inertness: an unconfigured id must change nothing ------------------------

def test_an_unset_home_channel_leaves_every_route_empty() -> None:
    """No home channel means no Slack target anywhere, never a stray id."""
    ctx = _resolve({})
    assert _deliver_targets(SPLUNK_STATUS, ctx, MAIN_TASKS) == "slack:"
    assert _deliver_targets(TRIAGE, ctx, MAIN_TASKS) == "slack:"
    assert _deliver_targets(KANBAN, ctx, MAIN_TASKS) == "slack:"
    match = re.search(DIRECT, DIRECT_TASKS)
    assert match, "the direct-cron default deliver expression moved"
    assert ctx[match.group(1)] == ""


def test_an_unset_issues_channel_disables_the_board_split() -> None:
    """Empty means "leave the failure lines inline", never "send them nowhere"."""
    ctx = _resolve({})
    assert ctx["hermes_agent_kanban_digest_issues_channel"] == ""


# --- ids never enter git -----------------------------------------------------

def test_every_channel_id_is_env_sourced_and_never_a_literal() -> None:
    for name, tpl in DEFAULTS.items():
        if "channel" not in name or not isinstance(tpl, str):
            continue
        assert not re.search(r"\bC0[A-Z0-9]{8,}\b", tpl), \
            f"{name} carries a literal Slack channel id"
    assert "lookup('env'" in str(DEFAULTS["hermes_agent_slack_home_channel"])
    assert "hermes_agent_slack_home_channel" in str(DEFAULTS["hermes_agent_slack_noise_channel"])
    assert "hermes_agent_slack_home_channel" in str(DEFAULTS["hermes_agent_slack_hermes_all_channel"])
    assert "lookup('env'" in str(DEFAULTS["hermes_agent_slack_issues_channel"])
    assert "lookup('env'" in str(DEFAULTS["hermes_agent_slack_splunk_channel"])


def test_the_dead_composite_deliver_layer_is_gone() -> None:
    """It resolved to nothing at runtime and hid the collapse behind apparent
    sophistication. If one comes back, it must come back wired to a call site."""
    for dead in ("hermes_agent_firehose_deliver",
                 "hermes_agent_hermes_all_deliver_suffix",
                 "hermes_agent_splunk_alert_deliver",
                 "hermes_agent_splunk_digest_deliver",
                 "hermes_agent_github_monitor_cron_deliver",
                 "hermes_agent_bot_pr_triage_cron_deliver",
                 "hermes_agent_docs_sync_cron_deliver",
                 "hermes_agent_summary_cron_deliver",
                 "hermes_agent_zammad_review_cron_deliver"):
        assert dead not in DEFAULTS, f"{dead} is back but still reaches no call site"


def test_every_channel_var_reaches_a_consumer() -> None:
    """The same defect class as the block above, caught one layer earlier.

    A channel var whose only readers are these tests routes nothing: it looks
    like a configured surface, resolves to a real Slack id, and no emitter ever
    posts to it. That is how the collapse hid — sophistication with no call
    site. A var must be read by a task, a template, or another var.
    """
    consumers = "\n".join(
        p.read_text()
        for d in ("tasks", "templates", "handlers", "vars")
        for p in sorted((ROLE / d).rglob("*"))
        if p.is_file()
    ) + "\n" + yaml.safe_dump(DEFAULTS)

    for name in DEFAULTS:
        if not (name.startswith("hermes_agent_slack_") and name.endswith("_channel")):
            continue
        # Its own definition is not a use, so require a second occurrence.
        assert consumers.count(name) > 1, (
            f"{name} is defined but nothing reads it — wire it to an emitter or "
            f"drop it; a channel with no producer is indistinguishable from a "
            f"broken one"
        )


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
