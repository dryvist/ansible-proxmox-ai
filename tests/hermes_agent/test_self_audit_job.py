"""The self-audit cron job is wired correctly and cannot silently collide.

The self-audit job (145-self-audit.yml, entry in 43-direct-cron-jobs-core.yml)
is the agent auditing its own operation — Slack read-back, own-error Splunk
sweep, subsystem probes, kanban/Zammad routing. Three failure modes are worth
a permanent guard:

1. A wiring typo that drops it out of the reconciled fleet (wrong list key,
   wrong var names) — it would simply never be created.
2. A name collision under the reconcile's substring existence test
   (docs/hermes-ops/cron-fleet.md: cron names must not be substrings of one
   another, because presence is checked by substring match against
   `cron list --all`).
3. A same-minute start collision with another enabled agentic job — the exact
   thing tests/hermes_agent/test_cron_stagger.py exists for; this re-checks it
   scoped to this job so a future schedule edit to either side is caught from
   both directions.

Runs bare (`python3 tests/hermes_agent/test_self_audit_job.py`) or under pytest.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from _role_files import role_defaults
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)

_JINJA_ENV = Environment(autoescape=False)


def _render(value: str) -> str:
    return _JINJA_ENV.from_string(value).render(**DEFAULTS)


def _self_audit_job() -> dict:
    jobs = [j for j in DEFAULTS["hermes_agent_direct_cron_jobs"]
            if _render(j["name"]) == DEFAULTS["hermes_agent_self_audit_cron_name"]]
    assert len(jobs) == 1, (
        f"expected exactly one self-audit entry in hermes_agent_direct_cron_jobs, "
        f"found {len(jobs)}"
    )
    return jobs[0]


def _expand_field(field: str, span: int) -> set[int]:
    out: set[int] = set()
    for part in field.split(","):
        if part == "*":
            out.update(range(span))
        elif part.startswith("*/"):
            out.update(range(0, span, int(part[2:])))
        elif "-" in part:
            lo, hi = part.split("-")
            out.update(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def _minutes(expr: str) -> set[int]:
    minute_field, hour_field = expr.split()[0], expr.split()[1]
    return {h * 60 + m
            for h in _expand_field(hour_field, 24)
            for m in _expand_field(minute_field, 60)}


def test_job_entry_is_present_and_correctly_shaped():
    job = _self_audit_job()
    assert _render(job["schedule"]) == DEFAULTS["hermes_agent_self_audit_cron_schedule"]
    assert _render(job["prompt_file"]) == "hermes-self-audit.md"
    assert _render(job["skill"]) == "dryvist/self-audit"
    # Findings belong on the issues channel, not hermes-all — this job's whole
    # point is defects, so a healthy run is [SILENT] and an unhealthy one pages.
    assert "issues_channel" in job["deliver"]


def test_enabled_gate_requires_its_inputs():
    """The gate must demand every capability the run needs: Splunk MCP (own-error
    sweep), Slack tokens (read-back + delivery), and a non-empty issues channel
    (its fixed deliver target). Zammad is deliberately absent — the skill
    degrades gracefully without it."""
    gate = _self_audit_job()["enabled"]
    for needle in ("hermes_agent_splunk_mcp_url",
                   "hermes_agent_slack_bot_token",
                   "hermes_agent_slack_app_token",
                   "hermes_agent_slack_issues_channel"):
        assert needle in gate, f"enabled gate does not require {needle}"


def test_name_is_not_a_substring_of_any_other_job_name():
    """The reconcile's existence test is a SUBSTRING match against
    `cron list --all` — a name contained in (or containing) another job's name
    makes create-if-absent lie about what already exists."""
    mine = DEFAULTS["hermes_agent_self_audit_cron_name"]
    others = {_render(j["name"]) for j in DEFAULTS["hermes_agent_direct_cron_jobs"]}
    others.discard(mine)
    clashes = {n for n in others if n in mine or mine in n}
    assert not clashes, f"substring collision between {mine!r} and {sorted(clashes)}"


def test_schedule_does_not_start_in_the_same_minute_as_another_enabled_job():
    mine = _minutes(DEFAULTS["hermes_agent_self_audit_cron_schedule"])
    for job in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        if job.get("enabled") is False:
            continue
        name = _render(job["name"])
        if name == DEFAULTS["hermes_agent_self_audit_cron_name"]:
            continue
        expr = _render(job["schedule"])
        if not re.match(r"^[\d*/,\- ]+$", expr) or len(expr.split()) != 5:
            continue  # non-cron-shaped schedules are stagger-test's problem
        overlap = mine & _minutes(expr)
        assert not overlap, (
            f"self-audit starts together with {name} at "
            f"{[f'{m // 60:02d}:{m % 60:02d}' for m in sorted(overlap)]}"
        )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
