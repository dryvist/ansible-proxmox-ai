"""fabric_watchdog absence-of-success contract.

The role's endpoint probes answer "is the fabric reachable" and stay green while
every scheduled job fails or stops firing altogether. A fleet in that state
posts an error per run — indistinguishable from ordinary noise — so nothing
pages. The doctrine that covers it: alert on the absence of success, not just on
errors.

These tests run the shipped script against a synthetic job store rather than
asserting on its text, and pin BOTH directions: it fires on a stalled job and
stays silent on a healthy one. A guard only ever observed staying quiet is not
evidence of anything.

Lives under tests/hermes_agent/ because fabric_watchdog runs on the Hermes guest
and reads that role's cron job store (see test_fabric_watchdog_debounce.py).
"""

from __future__ import annotations

import json
import re
import tempfile
import types
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/fabric_watchdog/templates/cron-success-watchdog.py.j2"
DEFAULTS = (REPO_ROOT / "roles/fabric_watchdog/defaults/main.yml").read_text()

TMP = Path(tempfile.mkdtemp(prefix="cron-success-watchdog-selfcheck-"))
NOW = 1785000000.0
MINUTE = 60.0

# Stand-ins for the values Ansible renders from defaults/main.yml. The numeric
# ones mirror the defaults; test_thresholds_match_the_shipped_defaults pins them
# together so a defaults tweak cannot leave these fixtures testing fiction.
FIXTURE_CONFIG = {
    "JOBS_FILE": str(TMP / "jobs.json"),
    "STATE_PATH": str(TMP / "cron-success.json"),
    "MISSED_CYCLES": 3,
    "FLOOR_MINUTES": 30,
    "CEILING_MINUTES": 1440,
    "FLEET_ROLLUP_AT": 5,
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


def run(jobs, state=None, now=NOW):
    """Evaluate a synthetic store. Returns (stale, recovered, state, log lines)."""
    Path(FIXTURE_CONFIG["JOBS_FILE"]).write_text(json.dumps({"jobs": jobs}))
    state = state if state is not None else {"schema": WD.STATE_SCHEMA, "jobs": {}}
    lines = []
    original, WD.log = WD.log, lines.append
    try:
        stale, recovered = WD.evaluate(WD.load_jobs(), state, now)
    finally:
        WD.log = original
    return stale, recovered, state, lines


def settle(jobs, now=NOW):
    """Establish a healthy baseline: first tick learns each job's last success.

    Asserts that tick was itself silent. Without this, a watchdog that calls
    EVERYTHING stale would fire here, latch, and then look correctly quiet on
    every later tick — which is how a quiet-direction test passes against a
    guard that is screaming. (An always-stale mutation did exactly that.)
    """
    stale, _, state, _ = run(jobs, now=now)
    assert stale == [], f"baseline tick was not quiet: {stale}"
    return state


# --- Fires when it should ----------------------------------------------------


def test_a_job_that_stopped_running_fires() -> None:
    """The core case: no error, no run, nothing but silence. A 15-minute job
    whose last success is ~3 hours old has missed a dozen cycles.
    """
    healthy = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(healthy)
    stale, _, _, _ = run(healthy, state, now=NOW + 175 * MINUTE)
    assert len(stale) == 1 and stale[0].startswith("digest ")


def test_a_job_failing_every_run_fires_even_though_last_run_at_stays_fresh() -> None:
    """The trap this exists for. `last_run_at` records a RUN, not a SUCCESS, so
    a job erroring on every tick keeps it perpetually fresh. Reading the store's
    timestamp directly would call that job healthy forever.
    """
    state = settle([job("triage", cadence_min=15, last_ok_ago_min=1)])
    later = NOW + 200 * MINUTE
    # Still running punctually — and failing every time.
    failing = [job("triage", cadence_min=15, last_ok_ago_min=-199, status="error")]
    stale, _, _, _ = run(failing, state, now=later)
    assert len(stale) == 1, "a job that runs but never succeeds must page"


def test_a_mass_stall_arrives_as_one_message_not_one_per_job() -> None:
    healthy = [job(f"j{i}", cadence_min=15, last_ok_ago_min=1) for i in range(6)]
    state = settle(healthy)
    stale, _, _, _ = run(healthy, state, now=NOW + 300 * MINUTE)
    assert len(stale) >= FIXTURE_CONFIG["FLEET_ROLLUP_AT"], (
        "six simultaneously stale jobs must reach the roll-up threshold, or the "
        "fleet-death case posts six separate pages"
    )


# --- Quiet when it should ----------------------------------------------------


def test_a_healthy_fleet_says_nothing() -> None:
    jobs = [
        job("fast", cadence_min=15, last_ok_ago_min=5),
        job("hourly", cadence_min=60, last_ok_ago_min=20),
        job("daily", cadence_min=1440, last_ok_ago_min=300),
    ]
    state = settle(jobs)
    stale, recovered, _, _ = run(jobs, state)
    assert stale == [] and recovered == []


def test_one_late_run_does_not_page() -> None:
    """MISSED_CYCLES > 1 exists so a single late run or one retry is absorbed."""
    state = settle([job("hourly", cadence_min=60, last_ok_ago_min=5)])
    stale, _, _, _ = run(
        [job("hourly", cadence_min=60, last_ok_ago_min=5)], state, now=NOW + 65 * MINUTE
    )
    assert stale == [], "a job one cycle late is late, not silent"


def test_a_daily_job_is_not_judged_by_a_frequent_jobs_clock() -> None:
    """The per-cadence rule's whole point: 90 minutes of silence is a stall for a
    15-minute job and entirely normal for a daily one.
    """
    daily = [job("daily", cadence_min=1440, last_ok_ago_min=5)]
    quarter = [job("quarter", cadence_min=15, last_ok_ago_min=5)]
    later = NOW + 90 * MINUTE
    assert run(daily, settle(daily), now=later)[0] == []
    assert len(run(quarter, settle(quarter), now=later)[0]) == 1


def test_a_disabled_job_is_not_silence() -> None:
    state = settle([job("seasonal", cadence_min=15, last_ok_ago_min=1)])
    off = [job("seasonal", cadence_min=15, last_ok_ago_min=1, enabled=False)]
    stale, _, _, _ = run(off, state, now=NOW + 999 * MINUTE)
    assert stale == [], "deliberately disabled is not a failure to succeed"


def test_a_brand_new_job_gets_one_full_threshold_before_it_can_page() -> None:
    """Seeding at `now` rather than paging immediately. Otherwise every deploy
    onto an already-broken fleet opens with an alert storm for silence that
    predates any observation this watchdog made.
    """
    stale, _, _, lines = run(
        [job("newborn", cadence_min=15, last_ok_ago_min=999, status="error")]
    )
    assert stale == []
    assert any("SEED" in line for line in lines)


# --- Re-alert suppression ----------------------------------------------------


def test_an_ongoing_stall_alerts_once_not_every_tick() -> None:
    """The alert must not become the noise it replaces."""
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    first, _, state, _ = run(jobs, state, now=NOW + 200 * MINUTE)
    assert len(first) == 1
    for tick in range(1, 6):
        again, _, state, _ = run(jobs, state, now=NOW + (200 + tick * 2) * MINUTE)
        assert again == [], f"tick {tick} re-announced an already-alerted stall"


def test_recovery_clears_the_latch_so_the_next_stall_can_page_again() -> None:
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    run(jobs, state, now=NOW + 200 * MINUTE)
    healthy = [job("digest", cadence_min=15, last_ok_ago_min=-201)]
    _, recovered, state, _ = run(healthy, state, now=NOW + 205 * MINUTE)
    assert recovered == ["digest"]
    stale, _, _, _ = run(healthy, state, now=NOW + 500 * MINUTE)
    assert len(stale) == 1, "a latch that never clears silences every later stall"


# --- Logging and threshold contracts -----------------------------------------


def test_every_job_gets_a_decision_line_every_tick() -> None:
    """A guard that exits silently hides the halt it exists to catch."""
    jobs = [job(f"j{i}", cadence_min=15, last_ok_ago_min=1) for i in range(4)]
    state = settle(jobs)
    _, _, _, lines = run(jobs, state)
    for i in range(4):
        assert any(line.startswith(f"j{i}: ") for line in lines), f"j{i} evaluated in silence"


def test_the_threshold_derives_from_the_jobs_own_cadence() -> None:
    quarter = WD.threshold_seconds(WD.cadence_seconds(job("a", cadence_min=15, last_ok_ago_min=0)))
    hourly = WD.threshold_seconds(WD.cadence_seconds(job("b", cadence_min=60, last_ok_ago_min=0)))
    assert hourly > quarter, "a slower job must be given a longer threshold"
    assert hourly == 60 * MINUTE * FIXTURE_CONFIG["MISSED_CYCLES"]


def test_a_cron_expression_job_derives_its_cadence_without_a_cron_parser() -> None:
    """Cron-kind jobs carry no `minutes`, so the cadence comes from the gap the
    scheduler already computed between last_run_at and next_run_at.
    """
    cron_job = {
        "id": "nightly", "name": "nightly", "enabled": True,
        "schedule": {"kind": "cron", "expression": "0 3 * * *"},
        "last_run_at": _iso(NOW), "next_run_at": _iso(NOW + 1440 * MINUTE),
        "last_status": "ok",
    }
    assert WD.cadence_seconds(cron_job) == 1440 * MINUTE


def test_thresholds_match_the_shipped_defaults() -> None:
    """Guard the values, not just the code shape — otherwise the per-cadence
    rule can be flattened back to a global one in a defaults tweak with every
    other test still green.
    """
    for name, fixture in FIXTURE_CONFIG.items():
        if not isinstance(fixture, int):
            continue
        var = "fabric_watchdog_success_" + name.lower()
        match = re.search(rf"^{var}:\s*(\d+)", DEFAULTS, re.M)
        assert match is not None, f"{var} missing from defaults — renamed?"
        assert int(match.group(1)) == fixture, f"{var} drifted from its self-check fixture"
    assert FIXTURE_CONFIG["MISSED_CYCLES"] >= 2, (
        "missed_cycles=1 pages on a single late run, which is the error-rate "
        "alerting this watchdog was built to replace"
    )
