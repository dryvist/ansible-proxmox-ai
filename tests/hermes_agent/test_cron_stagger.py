"""Two agentic cards must not be scheduled to start in the same minute.

The serving tier admits exactly ONE in-flight request per model
(``--decode-concurrency 1 --prompt-concurrency 1``) and rejects everything else
with an instant 429. The router absorbs those, but its tolerance is finite
(``rate_limit_retries`` x ``retry_after_seconds``), so simultaneous starts spend
that budget on each other instead of on a genuinely busy slot.

Measured on the serving host 2026-08-05: prompts of up to 59,418 tokens holding
the slot for one to two minutes, and eleven 429s inside three minutes.

Before this guard, minute :00 carried FOUR cards — including the only hourly
one, colliding with the ``*/15`` board digest 15 times a day — and a news
schedule added earlier the same day landed on :13 on top of anomaly-hunt without
anything noticing.

What this does NOT claim: staggering fixes contention. Cards here carry
``max_runtime`` of 25-45 minutes, so a long run still spans later starts. This
only stops the avoidable, free collisions.

Runs bare (``python3 tests/hermes_agent/test_cron_stagger.py``) or under pytest.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import yaml
from _role_files import role_defaults
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)

CRON_FIELD = re.compile(r"^[\d*/,\- ]+$")

_JINJA_ENV = Environment(autoescape=False)


def _render(value: str) -> str:
    """Resolve a job-entry field against the role's own defaults — the same
    data Ansible renders it from at converge time. A field is either a
    literal string (the assistant-identity jobs and `review` — its name
    lives in `130-workload-reframe.yml`, referenced by `43-*` as
    `hermes_agent_kanban_reviewer_job`) or a whole-value `{{ hermes_agent_x
    }}` reference (every other job); both render identically here."""
    return _JINJA_ENV.from_string(value).render(**DEFAULTS)


def _expand_field(field: str, span: int) -> set[int]:
    """Values a single cron field (comma list of `*`, `*/N`, `H`, `H1-H2`) fires on."""
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
    """Minute-of-day (hour*60 + minute) values a 5-field cron expression fires
    on. Hour-aware: two daily cards sharing a `:00`/`:13`/... minute field on
    DIFFERENT hours (e.g. 0 2 * * * vs 0 12 * * *) do not actually start
    together — matching on the minute field alone was the false-positive this
    produced once every converted card became live (they used to be excluded
    via hermes_agent_kanban_paused_jobs, a list this reframe removed)."""
    minute_field, hour_field = expr.split()[0], expr.split()[1]
    minutes = _expand_field(minute_field, 60)
    hours = _expand_field(hour_field, 24)
    return {h * 60 + m for h in hours for m in minutes}


def _agentic_jobs() -> dict[str, str]:
    """name -> cron schedule for every ENABLED entry in
    hermes_agent_direct_cron_jobs — the fleet's single source of truth —
    resolved directly from each entry's own `name`/`schedule` fields rather
    than reconstructed by matching two independently-derived, independently
    named lookups (a `*_cron_name`-shaped regex against the job list, a
    `*_cron_schedule`/`*_schedule`-suffixed variable scan against every
    default). That two-lookup shape is exactly what let a job through
    unchecked: a literal `name:`/`schedule:` pair (the three
    `assistant-*` jobs in `41-*`, and `review`, whose name is
    `hermes_agent_kanban_reviewer_job` — no `_cron_name` suffix) never
    matched either lookup's naming convention, so it silently sat outside
    both dicts and this test never saw it. It is how the collision between
    `assistant-daily-brief` and `daily-summary` (both `0 12 * * *` before
    the 2026-08 stagger pass) went unnoticed. Resolving each entry's own
    fields sidesteps the naming-convention guesswork entirely: literal or
    templated, every job renders through the same path.

    Script-fed crons are excluded: they call APIs, not the brain. Native-cron
    reframe (18/18): every former board card, including docs-sync, is now a
    hermes_agent_direct_cron_jobs entry — the gateway runs the prompt directly.
    hermes_agent_kanban_cards no longer exists. The retired ``-v2`` entries
    (``enabled: false`` literal, never reconciled) are excluded — there is no
    longer a separate paused-jobs list to check against.
    """
    jobs: dict[str, str] = {}
    for entry in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        if entry.get("enabled") is False:
            continue
        name = _render(entry["name"])
        schedule = _render(entry["schedule"])
        assert CRON_FIELD.match(schedule) and len(schedule.split()) == 5, (
            f"{name}: schedule {schedule!r} does not look like a 5-field cron expression"
        )
        jobs[name] = schedule
    return jobs


def test_no_two_active_agentic_cards_start_in_the_same_minute() -> None:
    """The avoidable collision. Two model-driven cards starting together burn
    the router's rate-limit budget on each other rather than on real work."""
    by_minute: dict[int, list[str]] = defaultdict(list)
    for name, expr in _agentic_jobs().items():
        for minute in _minutes(expr):
            by_minute[minute].append(name)

    clashes = {m: sorted(j) for m, j in by_minute.items() if len(j) > 1}
    assert not clashes, "\n".join(
        f"{m // 60:02d}:{m % 60:02d} starts {jobs} together"
        for m, jobs in sorted(clashes.items())
    )


def test_the_hourly_card_avoids_the_board_digest_tick() -> None:
    """The fabric-status card is the only hourly agentic one, so a collision it
    has repeats every single hour rather than once a day. The board digest runs
    on a fixed interval it cannot dodge, so the hourly card moves instead."""
    # The digest ticks every N minutes of every hour, so only fabric-status's
    # minute-of-HOUR matters here (unlike the minute-of-day check above).
    fabric = {m % 60 for m in _minutes(DEFAULTS["hermes_agent_daily_status_cron_schedule"])}
    digest_interval = int(DEFAULTS["hermes_agent_kanban_digest_interval_minutes"])
    digest = set(range(0, 60, digest_interval))
    assert not (fabric & digest), (
        f"fabric-status starts at {sorted(fabric)}, which overlaps the board "
        f"digest tick at {sorted(digest)}"
    )


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
