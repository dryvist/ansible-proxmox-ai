"""Shared harness for the fabric_watchdog absence-of-success self-checks.

Renders the shipped template with fixture config and imports it as a module, so
the tests exercise the real script rather than asserting on its text. Split out
of the test files it serves because a reader changing one behaviour should not
have to read every behaviour to find the fixture that builds a job record.

Consumed by test_fabric_watchdog_success_absence.py (the single-store contract)
and test_fabric_watchdog_success_stores.py (multiple roots and stopped stores).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/fabric_watchdog/templates/cron-success-watchdog.py.j2"
DEFAULTS = (REPO_ROOT / "roles/fabric_watchdog/defaults/main.yml").read_text()

TMP = Path(tempfile.mkdtemp(prefix="cron-success-watchdog-selfcheck-"))
# The default scheduler root, laid out as the guest lays it out: the store sits
# at <root>/cron/jobs.json and profile roots at <root>/profiles/<name>/cron/.
# The script derives its profile glob from the store path, so a fixture that
# flattened this would exercise a directory shape that does not exist.
(TMP / "cron").mkdir()
PROFILES_DIR = TMP / "profiles"
NOW = 1785000000.0
MINUTE = 60.0

# Stand-ins for the values Ansible renders from defaults/main.yml. The numeric
# ones mirror the defaults; test_thresholds_match_the_shipped_defaults pins them
# together so a defaults tweak cannot leave these fixtures testing fiction.
FIXTURE_CONFIG = {
    "JOBS_FILE": str(TMP / "cron" / "jobs.json"),
    "STATE_PATH": str(TMP / "cron-success.json"),
    "MISSED_CYCLES": 3,
    "FLOOR_MINUTES": 30,
    "CEILING_MINUTES": 1440,
    "FLEET_ROLLUP_AT": 5,
    "PAUSE_ALERT_MINUTES": 240,
}


def load_module():
    """Render the template's config lines to fixtures and import it as a module."""
    out = []
    for line in TEMPLATE_PATH.read_text().splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            assert name in FIXTURE_CONFIG, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {FIXTURE_CONFIG[name]!r}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("cron_success_watchdog")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


WD = load_module()


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def job(name, *, cadence_min, last_ok_ago_min, status="ok", **extra):
    """One store record, shaped as the scheduler writes it."""
    last = NOW - last_ok_ago_min * MINUTE
    record = {
        "id": name,
        "name": name,
        "enabled": True,
        "schedule": {"kind": "interval", "minutes": cadence_min},
        "last_run_at": _iso(last),
        "next_run_at": _iso(last + cadence_min * MINUTE),
        "last_status": status,
    }
    record.update(extra)
    return record


def run(jobs, state=None, now=NOW, profiles=None, paused=None):
    """Evaluate a synthetic fleet. Returns (stale, recovered, state, paused-too-long).

    `profiles` maps a profile name to its job list, or to None for a profile
    root that exists but carries no job store — the shape a profile has before
    anything is scheduled into it. `paused` names the roots that carry the stop
    sentinel; the sentinel is written as a real file so the script detects it the
    way it will on the guest, and its body is deliberately junk because presence
    alone is what engagement means.
    """
    Path(FIXTURE_CONFIG["JOBS_FILE"]).write_text(json.dumps({"jobs": jobs}))
    shutil.rmtree(PROFILES_DIR, ignore_errors=True)
    (TMP / "ESTOP").unlink(missing_ok=True)
    for name, profile_jobs in (profiles or {}).items():
        cron_dir = PROFILES_DIR / name / "cron"
        cron_dir.mkdir(parents=True)
        if profile_jobs is not None:
            (cron_dir / "jobs.json").write_text(json.dumps({"jobs": profile_jobs}))
    for name in paused or ():
        root = TMP if name == "default" else PROFILES_DIR / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "ESTOP").write_text("not-json {")
    state = state if state is not None else {"schema": WD.STATE_SCHEMA, "jobs": {}, "stores": {}}
    stale, recovered, paused_long = WD.evaluate(WD.load_stores(), state, now)
    return stale, recovered, state, paused_long


def log_lines(capsys):
    """What the script actually printed, with the prefix its `log()` adds stripped.

    Read from real stdout rather than by substituting the module's `log`. The
    logging requirement is that an operator sees a decision per job in journald,
    and only the shipped print path proves that — a replaced function proves the
    call happened, not that anything was emitted.
    """
    prefix = "cron-success-watchdog: "
    return [
        line[len(prefix) :]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(prefix)
    ]


def settle(jobs, now=NOW, profiles=None, paused=None):
    """Establish a healthy baseline: first tick learns each job's last success.

    Asserts that tick was itself silent. Without this, a watchdog that calls
    EVERYTHING stale would fire here, latch, and then look correctly quiet on
    every later tick — which is how a quiet-direction test passes against a
    guard that is screaming. (An always-stale mutation did exactly that.)
    """
    stale, _, state, _ = run(jobs, now=now, profiles=profiles, paused=paused)
    assert stale == [], f"baseline tick was not quiet: {stale}"
    return state
