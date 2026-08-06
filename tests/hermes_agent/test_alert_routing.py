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

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
FABRIC_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles" / "fabric_watchdog" / "defaults" / "main.yml").read_text()
)
MAIN_TASKS = (ROLE / "tasks" / "main.yml").read_text()
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


ENQUEUER = (ROLE / "templates" / "kanban-enqueue-recurring.sh.j2").read_text()
# The one line every recurring card is told to post its report with. A card may
# name a second destination for the all-healthy case, so this renders the whole
# instruction and reads back every destination in it rather than matching one.
CARD_REPORT_LINE = "hermes send --to slack:"


def _card(title: str) -> dict:
    for card in DEFAULTS["hermes_agent_kanban_cards"]:
        if card["title"] == title:
            return card
    raise AssertionError(f"no kanban card titled {title!r}")


def _render_card_report(title: str, ctx: dict[str, str]) -> str:
    """The delivery instruction as the enqueuer renders it for this card."""
    card = {
        key: _ENV.from_string(str(value)).render(**ctx).strip()
        if key in ("channel", "channel_when_healthy")
        else value
        for key, value in _card(title).items()
    }
    line = next((l for l in ENQUEUER.splitlines() if CARD_REPORT_LINE in l), None)
    assert line, "the card delivery instruction moved out of the enqueuer footer"
    return _ENV.from_string(line).render(**{**ctx, "card": card})


def _card_report_channels(title: str, ctx: dict[str, str]) -> list[str]:
    """Every Slack destination this card can post its report to, in render order."""
    return [f"slack:{c}" for c in re.findall(r"slack:(\S+)", _render_card_report(title, ctx))]


def _card_report_channel(title: str, ctx: dict[str, str]) -> str:
    """The single destination for a card that does not split by outcome."""
    targets = _card_report_channels(title, ctx)
    assert len(targets) == 1, f"{title} routes to {targets}; use _card_report_channels"
    return targets[0]


SPLUNK_STATUS = r'deliver: "(slack:\{\{ hermes_agent_splunk_status_digest_channel \}\})"'
KANBAN = r'deliver: "(slack:\{\{ hermes_agent_kanban_digest_channel \}\})"'
TRIAGE = r'deliver: "(slack:\{\{ hermes_agent_triage_channel \}\})"'
ZAMMAD_CLOSE = r'deliver: "(slack:\{\{ hermes_agent_slack_hermes_all_channel \}\})"'
DIRECT = r'hermes_agent_direct_deliver: "(slack:\{\{ hermes_agent_digest_slack_channel \}\})"'


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


# --- per-card routing: the recurring board is not one undifferentiated tier ----

def test_the_fabric_status_card_splits_its_report_by_outcome() -> None:
    """Healthy is FYI and goes to noise; broken is breakage and goes to issues.

    SUPERSEDES an earlier operator preference, recorded here so the reversal is
    not mistaken for drift. That rule sent BOTH halves to issues, so silence in
    that channel could be distinguished from a dead watchdog. It did not
    survive the cadence: this card runs hourly 08-22, so the all-clear half
    alone put ~15 identical "All systems operational" posts a day into the one
    channel that has to stay readable — and the operator's later, explicit rule
    is that nothing repeats in a core channel within 24 hours, with FYI going
    to noise.

    The dead-watchdog concern is now answered by the fabric_watchdog probe
    (asserted above) rather than by an hourly all-clear, and quiet in issues
    now MEANS healthy, which is the signal the all-clear was standing in for.
    """
    healthy, broken = _card_report_channels("Homelab AI fabric status", _resolve(CONFIGURED))
    assert healthy == "slack:C_NOISE"
    assert broken == "slack:C_ISSUES"


def test_an_unset_noise_channel_collapses_the_split_instead_of_dropping_posts() -> None:
    """Every channel var defaults empty and falls back to prior behaviour, so an
    unset id must never route a report to `slack:` with nothing after it. With
    the noise id absent the split disappears and everything goes to issues."""
    ctx = _resolve({**CONFIGURED, "SLACK_HERMES_NOISE_CHANNEL": ""})
    assert _card_report_channels("Homelab AI fabric status", ctx) == ["slack:C_ISSUES"]


def test_scouting_cards_report_to_the_noise_channel() -> None:
    """~28% of the audited corpus was low-urgency polling. These two are its
    recurring source: reading material on a fixed cadence, never an observation
    that something changed."""
    ctx = _resolve(CONFIGURED)
    for title in ("AI news scout", "Daily innovation proposal"):
        assert _card_report_channel(title, ctx) == "slack:C_NOISE", title


def test_cards_without_an_override_still_report_to_the_work_channel() -> None:
    """The override is opt-in. Every card that observes the estate — including
    the docs and Splunk fleets — stays on the work surface by omitting it."""
    ctx = _resolve(CONFIGURED)
    for title in ("Splunk triage sweep", "Docs sync", "Docs-site study"):
        assert "channel" not in _card(title), f"{title} gained an override"
        assert _card_report_channel(title, ctx) == "slack:C_ALL", title


def test_an_unset_override_target_falls_back_to_the_work_channel() -> None:
    """An override naming a channel the operator never configured must degrade to
    the work channel, never to `slack:` with an empty id."""
    ctx = _resolve({})
    for title in ("Homelab AI fabric status", "AI news scout"):
        assert _card_report_channel(title, ctx) == "slack:C_FIRE", title


def test_only_the_fabric_status_card_collapses_its_all_clear() -> None:
    """The operator wants #hermes-issues silent-but-alive: one line when healthy,
    detail when not. That inverts the report contract, so it is opt-in per card —
    for every other card a clean check IS the finding and must be named."""
    terse = [c["title"] for c in DEFAULTS["hermes_agent_kanban_cards"]
             if c.get("terse_when_healthy")]
    assert terse == ["Homelab AI fabric status"], terse

    branch = re.search(r"(\{% if card\.terse_when_healthy.*?\{% endif %\})", ENQUEUER, re.S)
    assert branch, "the report-shape branch is gone from the enqueuer footer"
    body = _ENV.from_string(branch.group(1))
    assert "All systems operational" in body.render(card={"terse_when_healthy": True})
    # The default branch must still forbid the unnamed "all healthy" summary that
    # the terse branch mandates — otherwise the opt-in silently became the norm.
    assert "without naming them" in body.render(card={})


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
    assert _deliver_targets(DIRECT, ctx, DIRECT_TASKS) == "slack:C_DIGEST"
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

    tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text())
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
        for t in yaml.safe_load(MAIN_TASKS)
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
