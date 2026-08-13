"""Shared fixtures for the Hermes alert-routing contract self-checks.

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
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import yaml
from jinja2 import Environment

from _role_files import role_defaults, role_tasks_text

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
# see the outcome-split tests in test_alert_routing_jobs.py.


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
# per-job (rendered separately by the per-job tests), so only the DEFAULT half
# (no per-job override) is checked with this pattern.
DIRECT = r"default\('slack:' ~ (hermes_agent_digest_slack_channel)\)"
