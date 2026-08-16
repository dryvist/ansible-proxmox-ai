"""Self-check for the Hermes per-job direct-cron routing, inertness, and
literal-id contracts.

Split from test_alert_routing.py to stay under the token budget — see
_alert_routing_shared.py for the shared resolve/deliver-target helpers,
test_alert_routing_channels.py for the four-way channel contract, and
test_alert_routing_helper.py for the runtime cron-route helper this leaves
behind.

Runs bare (`python3 tests/hermes_agent/test_alert_routing_jobs.py`) or under
pytest.
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
    _direct_deliver,
    _direct_job,
    _ENV,
    _resolve,
)


# --- per-job routing: the recurring fleet is not one undifferentiated tier ----
#
# Outcome-based split routing (channel_when_healthy) and terse_when_healthy
# are restored as PROMPT TEXT, not a hermes_agent_direct_cron_jobs field
# `--deliver` can express: `--deliver` is one fixed target (the breaking-run
# destination, issues, checked below), and the shared reporting footer
# (templates/direct-cron-footer.md.j2, appended to every job's prompt by
# reconcile_direct_cron.yml) instructs the model to self-route to
# channel_when_healthy via `hermes send` + a trailing [SILENT] on an
# all-clear run, so --deliver does not also post it.

# homelab-ai-fabric-status moved from the agentic direct-cron path to the
# script-fed enqueuer path (2026-08-16, fabric-status.py.j2) — see that
# template's own docstring for why (a real invented-endpoint false-outage
# alarm, and the resulting prompt/endpoints-override contradiction removed by
# construction). The outcome split (issues on unhealthy, terse-to-noise on
# healthy) is REIMPLEMENTED in the script itself now, not expressed via
# channel_when_healthy/terse_when_healthy — the enqueuer path has no
# equivalent fields for those. Its own self-check
# (`python3 -c` rendering the template with fixed values, run at development
# time — see the PR) pins both branches actually behaving that way; this file
# pins only the wiring: the job is no longer a direct_cron_jobs entry, and its
# --deliver default is the issues (breaking-run) channel.

def test_the_fabric_status_job_is_no_longer_agentic() -> None:
    """A naive port would have left the old direct-cron entry AND added the
    new enqueuer one — fighting each other every converge. There must be
    exactly one registration, and it is not this one."""
    marker = "{{ hermes_agent_daily_status_cron_name }}"
    for job in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        assert str(job.get("name", "")).strip() != marker, (
            "homelab-ai-fabric-status is still a direct_cron_jobs entry; "
            "it should only be reconciled via the script-fed enqueuer path now"
        )


FABRIC_STATUS = r'deliver: "(slack:\{\{ hermes_agent_slack_issues_channel \}\})"'


def test_the_fabric_status_script_deliver_is_the_breaking_run_channel() -> None:
    """The script suppresses this ([SILENT]) on its own healthy branch and
    posts terse-to-noise directly instead — see fabric-status.py.j2. --deliver
    itself is the unhealthy/breaking-run destination only."""
    ctx = _resolve(CONFIGURED)
    assert _deliver_targets(FABRIC_STATUS, ctx, MAIN_TASKS) == "slack:C_ISSUES"


def test_the_shared_footer_renders_the_outcome_split_and_the_default_case() -> None:
    """The footer template itself, not just the data feeding it — pins that
    the self-send + [SILENT] branch and the evidence contract are both
    actually present in the rendered text, for a job with the split and one
    without."""
    footer = (ROLE / "templates" / "direct-cron-footer.md.j2").read_text()
    split = _ENV.from_string(footer).render(
        item={"channel_when_healthy": "slack:C_NOISE", "terse_when_healthy": True},
        deliver="slack:C_ISSUES", ansible_managed="TEST",
    )
    default = _ENV.from_string(footer).render(item={}, deliver="slack:C_ALL", ansible_managed="TEST")
    assert "hermes send --to slack:C_NOISE" in split
    assert "[SILENT]" in split
    assert "All systems operational" in split
    assert "do not call `hermes send` yourself" in default
    for rendered in (split, default):
        assert "EVIDENCE CONTRACT" in rendered
        assert "do NOT invent a result" in rendered


def test_scouting_jobs_report_to_the_noise_channel() -> None:
    """~28% of the audited corpus was low-urgency polling. These two are its
    recurring source: reading material on a fixed cadence, never an observation
    that something changed."""
    ctx = _resolve(CONFIGURED)
    for var in ("hermes_agent_ai_news_cron_name", "hermes_agent_daily_innovation_cron_name"):
        assert _direct_deliver(var, ctx) == "slack:C_NOISE", var


def test_the_former_kanban_cards_still_report_to_the_work_channel() -> None:
    """Docs-site study and docs-sync were Kanban cards before the reframe
    (18/18 to cron); they are now direct-cron jobs but keep the same
    destination. Splunk triage was also one of them, but its domain-channel
    fix (below) moved it off the work channel onto the Splunk digest one."""
    ctx = _resolve(CONFIGURED)
    for var in ("hermes_agent_docs_study_cron_name", "hermes_agent_docs_sync_cron_name"):
        assert _direct_deliver(var, ctx) == "slack:C_ALL", var


def test_agentic_splunk_domain_crons_report_to_the_splunk_channel() -> None:
    """splunk-triage/security/parsing/deepdive and anomaly-hunt share the Splunk
    digest domain with the script-fed splunk-status/error/security digests
    (100-splunk.yml) — they must land on the same channel, not the work log."""
    ctx = _resolve(CONFIGURED)
    for var in ("hermes_agent_splunk_triage_cron_name", "hermes_agent_splunk_security_cron_name",
                "hermes_agent_splunk_parsing_cron_name", "hermes_agent_splunk_deepdive_cron_name",
                "hermes_agent_anomaly_hunt_cron_name"):
        assert _direct_deliver(var, ctx) == "slack:C_SPLUNK", var


def test_the_fabric_status_endpoints_are_pinned_in_the_script_not_guessed() -> None:
    """Measured 2026-07-31: the old agentic card's public catalog body named
    checks but no address, so the worker invented every one — localhost:8000/
    :8001 for services on other ports, localhost for two that live behind
    their own FQDNs, and a domain that does not resolve. All probes returned
    000 and it posted hourly total-outage alarms while mcp answered 406 and
    llm answered 401, their healthy codes. That fed the channel the operator
    wants near-silent, so a fabricated alarm was the one failure that made it
    worthless. fabric-status.py.j2 now hardcodes the same 3 endpoints
    (never the LLM router/MCP fabric — fabric_watchdog already covers those
    every 2 minutes) directly, with no prose layer a model could drift from."""
    script = (ROLE / "templates" / "fabric-status.py.j2").read_text()

    assert "{{ hermes_agent_api_server_port }}" in script
    assert "{{ hermes_agent_dashboard_port }}" in script
    for invented in ("localhost:8000", "localhost:8001", "localhost:6333", "localhost:8086"):
        assert invented not in script, f"{invented} was one of the invented endpoints"

    # Only a probe that never reached anything is a failure; any HTTP status
    # at all (including 401/403/404/406) means the service answered.
    assert "probe failed" in script

    # fabric_watchdog owns the external front doors; a second prober here
    # would only double-report.
    assert "llm front door" in script.lower() or "MCP fabric" in script
    assert "/v1/models" not in script

    # And it must actually reach the deployed guest: templated, not dead code.
    deploys = (ROLE / "tasks" / "script_deploys.yml").read_text()
    assert "fabric-status.py.j2" in deploys


# --- inertness: an unconfigured id must change nothing ------------------------

def test_unset_channels_reproduce_todays_routing_exactly() -> None:
    """Every new var defaults empty and falls back. An operator who deploys this
    without setting anything must see byte-identical behaviour, not 'close'."""
    ctx = _resolve({})
    assert _deliver_targets(SPLUNK_STATUS, ctx, MAIN_TASKS) == "slack:C_FIRE"
    assert _deliver_targets(TRIAGE, ctx, MAIN_TASKS) == "slack:C_DIGEST"
    assert _deliver_targets(KANBAN, ctx, MAIN_TASKS) == "slack:C_FIRE"
    match = re.search(DIRECT, DIRECT_TASKS)
    assert match, "the direct-cron default deliver expression moved"
    assert "slack:" + ctx[match.group(1)] == "slack:C_DIGEST"
    assert ctx["fabric_watchdog_alert_channel"] == "C_DIGEST"


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
    for var in ("hermes_agent_slack_issues_channel",
                "hermes_agent_slack_noise_channel",
                "hermes_agent_slack_splunk_channel",
                "hermes_agent_slack_hermes_all_channel"):
        assert "lookup('env'" in str(DEFAULTS[var]), f"{var} must be env-sourced"


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
