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

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "hermes_agent"
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())

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


def _minutes(expr: str) -> set[int]:
    """Minutes a 5-field cron expression fires on."""
    field = expr.split()[0]
    if field.startswith("*/"):
        step = int(field[2:])
        return set(range(0, 60, step))
    if field == "*":
        return set(range(60))
    out: set[int] = set()
    for part in field.split(","):
        if part.isdigit():
            out.add(int(part))
    return out


def _agentic_jobs() -> set[str]:
    """Cards that run a model, i.e. the ones that contend for the serving slot.

    Script-fed crons are excluded: they call APIs, not the brain.
    """
    paused = set(DEFAULTS["hermes_agent_kanban_paused_jobs"])
    names: set[str] = set()
    for card in DEFAULTS["hermes_agent_kanban_cards"]:
        if card["job"] in paused:
            continue
        job = card["job"]
        match = re.fullmatch(r"\{\{\s*hermes_agent_(\w+?)_cron_name\s*\}\}", job)
        names.add(match.group(1) if match else job)
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
        f"minute :{m:02d} starts {jobs} together" for m, jobs in sorted(clashes.items())
    )


def test_the_hourly_card_avoids_the_board_digest_tick() -> None:
    """The fabric-status card is the only hourly agentic one, so a collision it
    has repeats every single hour rather than once a day. The board digest runs
    on a fixed interval it cannot dodge, so the hourly card moves instead."""
    fabric = _minutes(DEFAULTS["hermes_agent_daily_status_cron_schedule"])
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
