"""Minimum inter-fire gap of a standard 5-field cron schedule.

Exists to derive hermes_agent_cron_wall_timeout_seconds (defaults/main/
20-brain-and-slack.yml) from the store's own job schedules instead of a
hand-picked constant. No cron-parsing library is already a dependency here
(no croniter, no filter_plugins precedent in this repo), and the schedules in
use are plain 5-field crontab syntax, so this is the whole of what's needed —
not a general crontab engine.

Only the minute and hour fields are considered. day-of-month/month/day-of-week
are ignored, which can only make the computed gap SMALLER than a schedule's
true firing gap (a weekly job's dow restriction is dropped, so it reads as
"fires every day at that minute/hour" instead of "fires once a week") — never
larger. Since callers take this as a divisor for a safety ceiling, erring
toward "looks more frequent than it is" is the safe direction; erring the
other way could let the ceiling exceed a job's real cadence, which is exactly
the starvation bug this exists to prevent.
"""

from __future__ import annotations


def _expand_field(field: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field (comma-list of N, N-M, */S, N-M/S, or *) to the
    set of concrete values it matches within [lo, hi].
    """
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        values.update(v for v in range(start, end + 1) if (v - start) % step == 0)
    return values


def cron_min_gap_minutes(schedule: str) -> int:
    """Minimum minutes between two firings of `schedule`, considering only
    the minute and hour fields, over one 1440-minute day (with wraparound).
    """
    minute_field, hour_field = schedule.split()[:2]
    minutes = _expand_field(minute_field, 0, 59)
    hours = _expand_field(hour_field, 0, 23)
    points = sorted({h * 60 + m for h in hours for m in minutes})
    if len(points) < 2:
        return 1440
    gaps = [b - a for a, b in zip(points, points[1:])]
    gaps.append(points[0] + 1440 - points[-1])
    return min(gaps)


class FilterModule:
    def filters(self):
        return {"cron_min_gap_minutes": cron_min_gap_minutes}


def _demo() -> None:
    """ponytail self-check: run directly (`python cron_schedule.py`) to verify."""
    cases = {
        "7 * * * *": 60,
        "4 8-22 * * *": 60,
        "19 0,12,16,19 * * *": 180,
        "22 */6 * * *": 360,
        "*/15 * * * *": 15,
        "0 2 * * *": 1440,
    }
    for schedule, expected in cases.items():
        got = cron_min_gap_minutes(schedule)
        assert got == expected, f"{schedule}: expected {expected}, got {got}"
    print(f"ok: {len(cases)} cases")


if __name__ == "__main__":
    _demo()
