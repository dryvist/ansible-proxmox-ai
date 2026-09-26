"""Jinja filter: which Hindsight worker ids are safe to decommission.

Parses `hindsight-admin worker-status`'s plain-text output (no --json exists
at the pinned tag — confirmed by reading hindsight_api/admin/cli.py) into
per-worker claimed-task ages, and returns only the worker ids that are BOTH
absent from the given inventory group AND show a positive dead signal from
Hindsight itself: every one of that worker's claimed tasks has gone at least
`dead_threshold_seconds` since its `last_update_ago` (now() - updated_at,
the exact field worker-status itself prints). Absence from inventory alone
is never enough — see the header comment on decommission_stale_workers.yml
for the same-batch-rename race this guards against.

Upstream ships no separate worker-heartbeat/lease column: confirmed by
reading hindsight_api/admin/cli.py at the pinned tag (no heartbeat/lease/
stale reference anywhere, and updated_at is only set at claim time and
completion, never touched mid-processing). Upstream's own docs instead name
this exact field as the dead-worker signal: "workers with a growing
last_update_ago are dead" (admin-cli.md), "Spot workers whose
last_update_ago keeps growing, indicating a dead or unresponsive worker"
(monitoring.md) — so that field is used here, not a heartbeat column that
does not exist.

A task line that fails to parse (unexpected format) is treated as fresh
(age 0) rather than dead — fail toward keeping a worker, never toward
decommissioning one on inconclusive evidence.
"""

from __future__ import annotations

import re

_WORKER_BLOCK_RE = re.compile(r"Worker: (\S+) \(\d+ task\(s\)\)\n((?:  .+\n)+)")
_LAST_UPDATE_RE = re.compile(r"last_update=(\S+) ago")
_TIMEDELTA_RE = re.compile(r"^(?:(\d+) days?, )?(\d+):(\d{2}):(\d{2})")


def _timedelta_str_to_seconds(text: str) -> int | None:
    """Parse a Python `str(timedelta)` rendering ("H:MM:SS[.ffffff]" or
    "D day(s), H:MM:SS[.ffffff]") to whole seconds, or None if unrecognized.
    """
    match = _TIMEDELTA_RE.match(text)
    if not match:
        return None
    days, hours, minutes, seconds = match.groups()
    return (int(days or 0) * 86400) + (int(hours) * 3600) + (int(minutes) * 60) + int(seconds)


def hindsight_stale_worker_ids(worker_status_stdout, inventory_group, dead_threshold_seconds):
    inventory_group = set(inventory_group or [])
    stale_ids = []
    for worker_id, tasks_text in _WORKER_BLOCK_RE.findall(worker_status_stdout or ""):
        if worker_id in inventory_group:
            continue
        ages = [_timedelta_str_to_seconds(m) for m in _LAST_UPDATE_RE.findall(tasks_text)]
        ages = [age if age is not None else 0 for age in ages]
        if ages and min(ages) > dead_threshold_seconds:
            stale_ids.append(worker_id)
    return stale_ids


class FilterModule:
    def filters(self):
        return {"hindsight_stale_worker_ids": hindsight_stale_worker_ids}


def _demo() -> None:
    """ponytail self-check: run directly (`python hindsight_stale_workers.py`)."""
    sample = (
        "Processing tasks across 4 worker(s):\n\n"
        "Worker: alive-present (1 task(s))\n"
        "  a1b2c3d4  retain               bank=demo  running=0:05:00  last_update=0:05:00 ago\n\n"
        "Worker: alive-absent (1 task(s))\n"
        "  b2c3d4e5  retain               bank=demo  running=0:05:00  last_update=0:05:00 ago\n\n"
        "Worker: dead-absent (1 task(s))\n"
        "  c3d4e5f6  consolidation        bank=demo  running=2:10:00  last_update=2:10:00 ago\n\n"
        "Worker: present-but-stale (1 task(s))\n"
        "  d4e5f6a7  reflect              bank=demo  running=5:00:00  last_update=5:00:00 ago\n\n"
    )
    inventory = ["alive-present", "present-but-stale"]
    got = hindsight_stale_worker_ids(sample, inventory, dead_threshold_seconds=4800)
    assert got == ["dead-absent"], got
    print(f"ok: stale={got}")


if __name__ == "__main__":
    _demo()
