"""Self-check for Hermes per-job direct-cron routing and profile homes.

Split from test_alert_routing.py to stay under the token budget — see
_alert_routing_shared.py for the shared resolve/deliver-target helpers,
test_alert_routing_channels.py for the one-channel contract,
test_alert_routing_profile_tokens.py for profile token isolation,
test_alert_routing_guards.py for channel configuration guards, and
test_alert_routing_helper.py for the runtime cron-route helper.

Runs bare (`python3 tests/hermes_agent/test_alert_routing_jobs.py`) or under
pytest.
"""

from __future__ import annotations

from jinja2 import Environment

from _alert_routing_shared import (
    CONFIGURED,
    DEFAULTS,
    MAIN_TASKS,
    ROLE,
    _direct_deliver,
    _direct_job,
    _resolve,
)
from test_profile_contract import (
    _RENDER_CONTEXT as _PROFILE_CONFIG_CONTEXT,
    _defaults as _profile_defaults,
    _jinja_env as _profile_jinja_env,
    _profiles,
)
from test_profile_env_parity import _context as _profile_env_context
from test_profile_env_parity import _env_values as _profile_env_values

PROFILE_CHANNELS = {
    "homelab-admin": "C_HOMELAB",
    "splunk-admin": "C_SPLUNK",
    "github-maint": "C_GITHUB",
    "orchestrator": "C_ORCHESTRATOR",
}

PROFILE_ROUTED_JOBS = {
    "homelab-admin": (
        "hermes_agent_zammad_review_cron_name",
        "hermes_agent_backlog_sweep_cron_name",
    ),
    "splunk-admin": (
        "hermes_agent_splunk_triage_cron_name",
        "hermes_agent_splunk_security_cron_name",
        "hermes_agent_splunk_parsing_cron_name",
        "hermes_agent_splunk_deepdive_cron_name",
        "hermes_agent_splunk_health_cron_name",
        "hermes_agent_router_capacity_digest_cron_name",
        "hermes_agent_llm_stats_hourly_cron_name",
    ),
    "github-maint": (
        "hermes_agent_github_maint_cron_name",
        "hermes_agent_pricing_diff_cron_name",
        "hermes_agent_github_cache_stats_cron_name",
    ),
    "orchestrator": (
        "hermes_agent_kanban_reviewer_job",
        "hermes_agent_fleet_health_cron_name",
    ),
}

ROOT_HOME_JOBS = (
    "hermes_agent_anomaly_hunt_cron_name",
    "hermes_agent_ai_news_cron_name",
    "hermes_agent_daily_innovation_cron_name",
    "hermes_agent_docs_study_cron_name",
    "hermes_agent_release_watch_cron_name",
    "hermes_agent_daily_status_cron_name",
)


def _routed_context() -> dict[str, str]:
    ctx = _resolve(CONFIGURED)
    ctx["hermes_agent_slack_profile_channels"] = PROFILE_CHANNELS
    ctx["hermes_agent_slack_profile_deliver_targets"] = {
        profile: f"slack:{channel}" for profile, channel in PROFILE_CHANNELS.items()
    }
    ctx["hermes_agent_kanban_orchestrator_profile"] = "orchestrator"
    return ctx


# --- per-job routing: the recurring fleet is not one undifferentiated tier ----
#
# `--deliver` carries findings to the agent's home channel. When
# `channel_when_healthy` is that same destination, the catalog's quiet footer
# must end with bare [SILENT] and leave the channel empty.

def test_the_fabric_status_job_deliver_is_the_home_work_channel() -> None:
    ctx = _resolve(CONFIGURED)
    assert _direct_deliver("hermes_agent_daily_status_cron_name", ctx) == "slack:C_HOME"


def test_the_fabric_status_job_carries_the_outcome_split() -> None:
    job = _direct_job("hermes_agent_daily_status_cron_name")
    assert "channel_when_healthy" in job
    assert job["channel_when_healthy"] == "slack:{{ hermes_agent_slack_noise_channel }}"
    ctx = _resolve(CONFIGURED)
    assert _direct_deliver("hermes_agent_daily_status_cron_name", ctx) == "slack:C_HOME"
    assert ctx["hermes_agent_slack_noise_channel"] == ctx["hermes_agent_slack_hermes_all_channel"]
    others = [j for j in DEFAULTS["hermes_agent_direct_cron_jobs"]
              if j is not job and "channel_when_healthy" in j]
    assert others == []


def test_the_footer_task_picks_the_catalog_footer_by_outcome_split() -> None:
    """The footer text lives in the prompt catalog, not in this role. Pins
    that the append task loads the quiet-when-healthy catalog footer for a
    job with channel_when_healthy and the plain one otherwise, and fills the
    two envsubst variables those footers declare."""
    task = (ROLE / "tasks" / "reconcile_direct_cron.yml").read_text()
    assert "hermes-direct-cron-footer-quiet.md' if item.channel_when_healthy is defined" in task
    assert "else 'hermes-direct-cron-footer.md'" in task
    assert "replace('${DELIVER}', hermes_agent_direct_deliver)" in task
    assert "replace('${CHANNEL_WHEN_HEALTHY}', item.channel_when_healthy | default(''))" in task
    assert not (ROLE / "templates" / "direct-cron-footer.md.j2").exists()


def test_scouting_jobs_report_to_the_home_channel() -> None:
    """~28% of the audited corpus was low-urgency polling. These two are its
    recurring source: reading material on a fixed cadence, never an observation
    that something changed."""
    ctx = _resolve(CONFIGURED)
    for var in ("hermes_agent_ai_news_cron_name", "hermes_agent_daily_innovation_cron_name"):
        assert _direct_deliver(var, ctx) == "slack:C_HOME", var


def test_the_former_kanban_cards_report_to_the_home_channel() -> None:
    """Docs-site study and docs-sync were Kanban cards before the reframe
    (18/18 to cron); they are now direct-cron jobs but keep the same
    destination. Splunk triage was also one of them, but its domain-channel
    fix (below) moved it off the work channel onto the Splunk digest one."""
    ctx = _resolve(CONFIGURED)
    for var in ("hermes_agent_docs_study_cron_name", "hermes_agent_docs_sync_cron_name"):
        assert _direct_deliver(var, ctx) == "slack:C_HOME", var


def test_splunk_findings_and_anomaly_hunt_route_by_reader() -> None:
    """Named Splunk findings use their routed profile channel; anomaly-hunt
    stays in the root home and keeps its operator-facing home destination."""
    ctx = _routed_context()
    for var in ("hermes_agent_splunk_triage_cron_name", "hermes_agent_splunk_security_cron_name",
                "hermes_agent_splunk_parsing_cron_name", "hermes_agent_splunk_deepdive_cron_name"):
        assert _direct_deliver(var, ctx) == "slack:C_SPLUNK", var
    assert _direct_deliver("hermes_agent_anomaly_hunt_cron_name", ctx) == "slack:C_HOME"


def test_profile_cron_homes_and_delivery_targets_match_the_decision() -> None:
    ctx = _routed_context()
    assert sum(map(len, PROFILE_ROUTED_JOBS.values())) == 14
    assert len(ROOT_HOME_JOBS) == 6

    for profile, cron_names in PROFILE_ROUTED_JOBS.items():
        route_target = f"slack:{PROFILE_CHANNELS[profile]}"
        route_key = (
            "hermes_agent_kanban_orchestrator_profile" if profile == "orchestrator" else repr(profile)
        )
        for cron_name in cron_names:
            job = _direct_job(cron_name)
            assert "hermes_agent_slack_profile_deliver_targets" in str(job["deliver"]), cron_name
            home = Environment().from_string(str(job["hermes_home"])).render(
                hermes_agent_hermes_home="/hermes-home",
                hermes_agent_kanban_orchestrator_profile="orchestrator",
            )
            assert home == f"/hermes-home/profiles/{profile}", cron_name
            assert _direct_deliver(cron_name, ctx) == route_target, cron_name
            if job.get("enabled") is not False:
                assert f"{route_key} in hermes_agent_slack_profile_deliver_targets" in str(
                    job["enabled"]
                ), cron_name

    for cron_name in ROOT_HOME_JOBS:
        job = _direct_job(cron_name)
        assert "hermes_home" not in job, cron_name
        assert _direct_deliver(cron_name, ctx) == "slack:C_HOME", cron_name


def test_github_maint_cron_stays_disabled_without_its_read_token() -> None:
    job = _direct_job("hermes_agent_github_maint_cron_name")
    context = {
        "hermes_agent_github_maint_enabled": True,
        "hermes_agent_github_identity_enabled": True,
        "hermes_agent_github_read_token": "",
        "hermes_agent_slack_bot_token": "xoxb-test",
        "hermes_agent_slack_app_token": "xapp-test",
        "hermes_agent_slack_profile_deliver_targets": {"github-maint": "slack:C_GITHUB"},
    }
    env = Environment()
    env.filters["bool"] = bool
    enabled = env.from_string(str(job["enabled"]))

    assert enabled.render(**context).strip() == "False"
    context["hermes_agent_github_read_token"] = "read-only-test-token"
    assert enabled.render(**context).strip() == "True"


def test_release_watch_uses_the_agent_home_channel() -> None:
    ctx = _resolve(CONFIGURED)
    job = _direct_job("hermes_agent_release_watch_cron_name")
    assert _direct_deliver("hermes_agent_release_watch_cron_name", ctx) == "slack:C_HOME"
    assert "channel_when_healthy" not in job


def test_the_fabric_status_card_is_told_its_endpoints_instead_of_guessing() -> None:
    """Measured 2026-07-31: the public catalog body names checks but no address,
    so the worker invented every one — localhost:8000/:8001 for services on other
    ports, localhost for two that live behind their own FQDNs, and a domain that
    does not resolve. All probes returned 000 and it posted hourly total-outage
    alarms while MCP answered 406 and the LLM endpoint answered 401, which only
    proved reachability and did not establish health.

    That card feeds the channel the operator wants near-silent, so a fabricated
    alarm is the one failure that makes the channel worthless."""
    ep = str(DEFAULTS["hermes_agent_daily_status_endpoints"])

    # Ports come from the deploy-time variables, never a literal a model can drift
    # away from — and never the invented ones.
    assert "{{ hermes_agent_api_server_port }}" in ep
    assert "{{ hermes_agent_dashboard_port }}" in ep
    for invented in ("localhost:8000", "localhost:8001", "localhost:6333", "localhost:8086"):
        assert invented not in ep, f"{invented} was one of the invented endpoints"

    assert "NOTHING ELSE" in ep and "Do not guess" in ep
    # 000 is an unanswered probe, not proof of a service outage.
    assert "HTTP 000 means the probe got no response" in " ".join(ep.split())
    assert "report it as unverified" in " ".join(ep.split())

    # fabric_watchdog owns the external front doors on a 2-minute cadence and
    # alerts to this same channel; a second prober here only double-reports.
    assert "Do NOT probe the MCP fabric or the LLM front door" in ep
    assert "/v1/models" not in ep

    # And it must actually reach the card: appended to the catalog body at load.
    assert "hermes_agent_daily_status_endpoints" in MAIN_TASKS


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
