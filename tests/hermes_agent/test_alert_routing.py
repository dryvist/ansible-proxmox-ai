"""Every Hermes emitter lands on the surface a human would look for it on.

A 13-day audit of 1084 messages (2026-07-18..07-31) found the work channel was
~52% Hermes reporting its own breakage and ~28% null-result polling, leaving
~10-17% actual work. The cause was configuration, not code: the firehose, digest
and hermes_all ids all resolved to ONE channel, and hermes_all was defined as an
alias of the firehose, so no config change could separate them.

These tests pin the four-way contract that replaced it:

    #hermes-all     real thinking, work, and findings
    #hermes-issues  Hermes itself not functional
    #hermes-splunk  the Splunk digest domain
    #hermes-noise   "no change since" / heartbeat polls

Two things this file deliberately does NOT do.

It does not test the composite ``*_deliver`` variables that used to live in
defaults — ``firehose_deliver``, ``hermes_all_deliver_suffix``,
``splunk_alert_deliver``, ``splunk_digest_deliver`` and the five
``*_cron_deliver`` aliases. Those were removed because **no live task ever read
them**: every real delivery resolves through the leaf channel vars asserted
below. Testing them proved a routing tier that did not run, which is why the
collapse went unnoticed. Assertions here bind to the actual ``deliver:`` lines
in the task files, so a call site that stops using its variable fails here.

It does not route on message keywords. The discriminator is the observation
path: Hermes reporting a failure it observed is work, Hermes failing to observe
is breakage. A keyword rule on "error"/"litellm" would misroute the most
valuable posts in the corpus — Splunk-observed litellm errors on the llm-routers
are findings produced by a job that worked.

Runs bare (`python3 tests/hermes_agent/test_alert_routing.py`) or under pytest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import yaml
from jinja2 import Environment
from _role_files import role_defaults, role_tasks, role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)
FABRIC_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles" / "fabric_watchdog" / "defaults" / "main.yml").read_text()
)
MAIN_TASKS = role_tasks_text(ROLE)
DIRECT_TASKS = (ROLE / "tasks" / "reconcile_direct_cron.yml").read_text()

_ENV = Environment(autoescape=False)

# The four channels, plus the two legacy ids they were collapsed onto.
CONFIGURED = {
    "SLACK_HERMES_ALL_CHANNEL": "C_ALL",
    "SLACK_HERMES_ISSUES_CHANNEL": "C_ISSUES",
    "SLACK_HERMES_SPLUNK_CHANNEL": "C_SPLUNK",
    "SLACK_HERMES_NOISE_CHANNEL": "C_NOISE",
}


def _resolve(env_overrides: dict[str, str]) -> dict[str, str]:
    """Render every channel var against a simulated environment.

    Mirrors what Ansible does at converge: each var is a Jinja expression over
    the others, rooted in ``lookup('env', ...)``. Rendering iteratively lets a
    var reference another without hard-coding the dependency order here.
    """
    env = {
        "SLACK_FIREHOSE_CHANNEL": "C_FIRE",
        "HERMES_SLACK_DIGEST_CHANNEL": "C_DIGEST",
        **env_overrides,
    }
    def _lookup(plugin: str, name: str) -> str:
        """Stand in for Ansible's lookup plugin against the simulated env.

        A real callable rather than a regex substitution on the source text:
        the channel vars build their env-var NAME from the agent identity
        (`'SLACK_' ~ (hermes_agent_id | upper) ~ '_ISSUES_CHANNEL'`), so there
        is no literal in the source to match. Evaluating the expression is also
        what the converge actually does, which is the behaviour worth pinning.
        """
        assert plugin == "env", f"only the env lookup is simulated, got {plugin!r}"
        return env.get(name, "")

    # hermes_agent_id leads: every channel name below derives its env-var name
    # from it, so it has to be in the context before the first channel renders.
    names = (
        ["hermes_agent_id"]
        + [k for k in DEFAULTS if "channel" in k]
        + ["fabric_watchdog_alert_channel"]
    )
    source = {**DEFAULTS, "fabric_watchdog_alert_channel": FABRIC_DEFAULTS["fabric_watchdog_alert_channel"]}
    ctx: dict[str, str] = {}
    for _ in range(4):  # a few passes is plenty for this shallow dependency graph
        for name in names:
            tpl = str(source[name])
            ctx[name] = _ENV.from_string(tpl).render(**ctx, lookup=_lookup).strip()
    return ctx


def _deliver_targets(pattern: str, ctx: Mapping[str, object], text: str) -> str:
    """Render the `deliver:` value at a real call site."""
    match = re.search(pattern, text)
    assert match, f"call site not found — did the task move? {pattern}"
    return _ENV.from_string(match.group(1)).render(**ctx).strip()


# kanban-enqueue-recurring.sh.j2 is GONE (native-cron reframe, 18/18). It used
# to be the one enqueuer script for every recurring card, with per-card
# `channel` / `channel_when_healthy` / `terse_when_healthy` outcome-based split
# routing. All 18 cards it enqueued, including the former "Docs sync" card,
# are now plain `hermes_agent_direct_cron_jobs` entries — the gateway runs the
# prompt itself and delivers straight to Slack via one fixed `deliver:` per job
# (tasks/reconcile_direct_cron.yml). hermes_agent_kanban_cards no longer
# exists. `--deliver` cannot express a per-outcome split itself, but the split
# is restored as shared prompt text (templates/direct-cron-footer.md.j2) —
# see the outcome-split tests below.


def _direct_job(cron_name_var: str) -> dict:
    """The hermes_agent_direct_cron_jobs entry whose name is `{{ <var> }}`."""
    marker = "{{ " + cron_name_var + " }}"
    for job in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        if str(job.get("name", "")).strip() == marker:
            return job
    raise AssertionError(f"no direct cron job named via {cron_name_var!r}")


def _direct_deliver(cron_name_var: str, ctx: dict[str, str]) -> str:
    """The single, fixed `deliver:` target for a direct-cron job."""
    return _ENV.from_string(str(_direct_job(cron_name_var)["deliver"])).render(**ctx).strip()


SPLUNK_STATUS = r'deliver: "(slack:\{\{ hermes_agent_splunk_status_digest_channel \}\})"'
KANBAN = r'deliver: "(slack:\{\{ hermes_agent_kanban_digest_channel \}\})"'
TRIAGE = r'deliver: "(slack:\{\{ hermes_agent_triage_channel \}\})"'
ZAMMAD_CLOSE = r'deliver: "(slack:\{\{ hermes_agent_slack_hermes_all_channel \}\})"'
# The fallback deliver expression is `item.deliver | default('slack:' ~ var)`,
# not a standalone quoted string like the other call sites — item.deliver is
# per-job (rendered separately by the per-job tests above), so only the
# DEFAULT half (no per-job override) is checked with this pattern.
DIRECT = r"default\('slack:' ~ (hermes_agent_digest_slack_channel)\)"


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

def test_the_fabric_status_job_deliver_is_the_breaking_run_channel() -> None:
    """`--deliver` (issues) is the default/breaking-run destination; the
    all-clear destination (noise) is on the item as channel_when_healthy and
    is only reachable via the prompt footer's self-send branch, not --deliver
    itself — see test_the_fabric_status_job_carries_the_outcome_split below."""
    ctx = _resolve(CONFIGURED)
    assert _direct_deliver("hermes_agent_daily_status_cron_name", ctx) == "slack:C_ISSUES"


def test_the_fabric_status_job_carries_the_outcome_split() -> None:
    job = _direct_job("hermes_agent_daily_status_cron_name")
    assert "channel_when_healthy" in job
    assert job["channel_when_healthy"] == "slack:{{ hermes_agent_slack_noise_channel }}"
    assert job.get("terse_when_healthy") is True
    # No other job carries the split — it was one card's behaviour, not a
    # general one.
    others = [j for j in DEFAULTS["hermes_agent_direct_cron_jobs"]
              if j is not job and "channel_when_healthy" in j]
    assert others == [], [j.get("name") for j in others]


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
    """Splunk triage sweep, Docs-site study and docs-sync were Kanban cards
    before the reframe (18/18 to cron); they are now direct-cron jobs but keep
    the same destination."""
    ctx = _resolve(CONFIGURED)
    for var in ("hermes_agent_splunk_triage_cron_name", "hermes_agent_docs_study_cron_name",
                "hermes_agent_docs_sync_cron_name"):
        assert _direct_deliver(var, ctx) == "slack:C_ALL", var


def test_the_fabric_status_card_is_told_its_endpoints_instead_of_guessing() -> None:
    """Measured 2026-07-31: the public catalog body names checks but no address,
    so the worker invented every one — localhost:8000/:8001 for services on other
    ports, localhost for two that live behind their own FQDNs, and a domain that
    does not resolve. All probes returned 000 and it posted hourly total-outage
    alarms while mcp answered 406 and llm answered 401, their healthy codes.

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
    # 000 is a failed probe, not a down service. Reporting it as an outage is what
    # produced the alarms.
    assert "probe failed" in ep

    # fabric_watchdog owns the external front doors on a 2-minute cadence and
    # alerts to this same channel; a second prober here only double-reports.
    assert "Do NOT probe the MCP fabric or the LLM front door" in ep
    assert "/v1/models" not in ep

    # And it must actually reach the card: appended to the catalog body at load.
    assert "hermes_agent_daily_status_endpoints" in MAIN_TASKS


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


# --- the failure-routing helper, executed as shipped -------------------------
# The block below is patched into upstream's scheduler at converge. Rendering
# and running it here means these assertions exercise the real function, not a
# restatement of it.

def _load_route_helper(issues_target: str, marker: str = "[ISSUES]"):
    import logging
    import types

    tasks = role_tasks(ROLE)
    block = next(
        t["ansible.builtin.blockinfile"]["block"]
        for t in tasks
        if t.get("name") == "Patch Hermes cron delivery with a tool-call markup guard"
    )
    rendered = _ENV.from_string(block).render(
        hermes_agent_cron_failure_deliver=issues_target,
        hermes_agent_cron_issues_marker=marker,
    )
    mod = types.ModuleType("cron_guard")
    mod.__dict__.update(re=re, logger=logging.getLogger("test"))
    exec(compile(rendered, "cron-guard", "exec"), mod.__dict__)  # noqa: S102
    return mod


def test_a_successful_run_keeps_its_own_channel() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    routed, content = mod._cron_route(job, True, "12 indexes healthy")
    assert routed["deliver"] == "slack:C_SPLUNK"
    assert content == "12 indexes healthy"


def test_a_successful_run_reporting_an_observed_error_is_not_rerouted() -> None:
    """The regression a keyword rule would cause. A Splunk-observed litellm
    error on the llm-routers is a finding, produced by a job that worked — the
    single most valuable message shape in the audited corpus."""
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    routed, _ = mod._cron_route(
        job, True, "litellm.RateLimitError on llm-router-2 — 52 events, up from 18")
    assert routed["deliver"] == "slack:C_SPLUNK", "an observed error is a finding"


def test_a_failed_run_is_rerouted_to_the_issues_channel() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "splunk-error-digest", "deliver": "slack:C_SPLUNK"}
    routed, _ = mod._cron_route(job, False, "Cron failed: litellm.BadRequestError")
    assert routed["deliver"] == "slack:C_ISSUES"


def test_a_script_that_declares_its_own_failure_is_rerouted_and_unmarked() -> None:
    """Script-fed crons exit 0 and print their failure, so `success` is True."""
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "kanban-digest", "deliver": "slack:C_ALL"}
    routed, content = mod._cron_route(
        job, True, "[ISSUES] :warning: Splunk digest FAILED: 401 Unauthorized")
    assert routed["deliver"] == "slack:C_ISSUES"
    assert content.startswith(":warning:"), "the marker must never reach Slack"
    assert "[ISSUES]" not in content


def test_routing_never_mutates_the_callers_job() -> None:
    mod = _load_route_helper("slack:C_ISSUES")
    job = {"id": "j", "deliver": "slack:C_ALL"}
    mod._cron_route(job, False, "boom")
    assert job["deliver"] == "slack:C_ALL", "the scheduler reuses this dict"


def test_an_unset_issues_target_leaves_every_result_where_it_was() -> None:
    mod = _load_route_helper("")
    job = {"id": "j", "deliver": "slack:C_ALL"}
    routed, content = mod._cron_route(job, False, "[ISSUES] boom")
    assert routed["deliver"] == "slack:C_ALL"
    assert content == "boom", "the marker is stripped even when routing is off"


def test_the_marker_has_one_definition_shared_by_producer_and_consumer() -> None:
    """Two hard-coded copies would drift and silently stop routing."""
    block = next(
        t["ansible.builtin.blockinfile"]["block"]
        for t in role_tasks(ROLE)
        if t.get("name") == "Patch Hermes cron delivery with a tool-call markup guard"
    )
    assert "{{ hermes_agent_cron_issues_marker }}" in block
    for tpl in ("kanban-digest.py.j2", "splunk-digest.py.j2",
                "splunk-triage.py.j2", "zammad-auto-close.py.j2"):
        src = (ROLE / "templates" / tpl).read_text()
        assert 'ISSUES_MARKER = "{{ hermes_agent_cron_issues_marker }}"' in src, tpl
        assert "{ISSUES_MARKER}" in src, f"{tpl} declares the marker but never emits it"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
