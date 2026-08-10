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

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = role_defaults(ROLE)

CRON_FIELD = re.compile(r"^[\d*/,\- ]+$")


def _schedules() -> dict[str, str]:
    return {
        name.replace("hermes_agent_", "").replace("_cron_schedule", ""): value
        for name, value in DEFAULTS.items()
        if name.startswith("hermes_agent_")
        and name.endswith(("_cron_schedule", "_schedule"))
        and isinstance(value, str)
        and CRON_FIELD.match(value)
        and len(value.split()) == 5
    }


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


def _agentic_jobs() -> set[str]:
    """Cards that run a model, i.e. the ones that contend for the serving slot.

    Script-fed crons are excluded: they call APIs, not the brain. Native-cron
    reframe (18/18): every former board card, including docs-sync, is now a
    hermes_agent_direct_cron_jobs entry — the gateway runs the prompt directly.
    hermes_agent_kanban_cards no longer exists. The retired ``-v2`` entries
    (``enabled: false`` literal, never reconciled) are excluded — there is no
    longer a separate paused-jobs list to check against.
    """
    names: set[str] = set()
    for entry in DEFAULTS["hermes_agent_direct_cron_jobs"]:
        if entry.get("enabled") is False:
            continue
        key = entry.get("name", entry.get("job"))
        match = re.fullmatch(r"\{\{\s*hermes_agent_(\w+?)_cron_name\s*\}\}", key)
        names.add(match.group(1) if match else key)
    return names


def test_no_two_active_agentic_cards_start_in_the_same_minute() -> None:
    """The avoidable collision. Two model-driven cards starting together burn
    the router's rate-limit budget on each other rather than on real work."""
    schedules = _schedules()
    agentic = _agentic_jobs()

    by_minute: dict[int, list[str]] = defaultdict(list)
    for name, expr in schedules.items():
        if name not in agentic:
            continue
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
