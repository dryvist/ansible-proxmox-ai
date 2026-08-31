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


# --- Fires when it should ----------------------------------------------------


def test_a_job_that_stopped_running_fires() -> None:
    """The core case: no error, no run, nothing but silence. A 15-minute job
    whose last success is ~3 hours old has missed a dozen cycles.
    """
    healthy = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(healthy)
    stale, _, _, _ = run(healthy, state, now=NOW + 175 * MINUTE)
    assert len(stale) == 1 and stale[0].startswith("default/digest ")


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


def test_a_brand_new_job_gets_one_full_threshold_before_it_can_page(capsys) -> None:
    """Seeding at `now` rather than paging immediately. Otherwise every deploy
    onto an already-broken fleet opens with an alert storm for silence that
    predates any observation this watchdog made.
    """
    stale, _, _, _ = run([job("newborn", cadence_min=15, last_ok_ago_min=999, status="error")])
    assert stale == []
    assert any("SEED" in line for line in log_lines(capsys))


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
    assert recovered == ["default/digest"]
    stale, _, _, _ = run(healthy, state, now=NOW + 500 * MINUTE)
    assert len(stale) == 1, "a latch that never clears silences every later stall"


# --- Logging and threshold contracts -----------------------------------------


def test_every_job_gets_a_decision_line_every_tick(capsys) -> None:
    """A guard that exits silently hides the halt it exists to catch."""
    jobs = [job(f"j{i}", cadence_min=15, last_ok_ago_min=1) for i in range(4)]
    state = settle(jobs)
    log_lines(capsys)  # discard the baseline tick, or IT could satisfy the assertion
    run(jobs, state)
    lines = log_lines(capsys)
    for i in range(4):
        assert any(line.startswith(f"default/j{i}: ") for line in lines), f"j{i} evaluated in silence"


def test_the_threshold_derives_from_the_jobs_own_cadence() -> None:
    quarter = WD.threshold_seconds(WD.cadence_seconds(job("a", cadence_min=15, last_ok_ago_min=0)))
    hourly = WD.threshold_seconds(WD.cadence_seconds(job("b", cadence_min=60, last_ok_ago_min=0)))
    assert hourly > quarter, "a slower job must be given a longer threshold"
    assert hourly == 60 * MINUTE * FIXTURE_CONFIG["MISSED_CYCLES"]


def cron_job(name, *, cadence_min, last_ago_min, next_in_min, status="ok"):
    """A cron-kind record, whose schedule carries an expression and no `minutes`.

    last_run_at and next_run_at are set independently so the re-armed case —
    a stale last run with an advancing next run — can be built.
    """
    return {
        "id": name, "name": name, "enabled": True,
        "schedule": {"kind": "cron", "expr": f"26 */{cadence_min // 60} * * *"},
        "last_run_at": _iso(NOW - last_ago_min * MINUTE),
        "next_run_at": _iso(NOW + next_in_min * MINUTE),
        "last_status": status,
    }


def test_a_cron_expression_job_derives_its_cadence_without_a_cron_parser() -> None:
    """Cron-kind jobs carry no `minutes`, so the cadence comes from the gap the
    scheduler already computed between last_run_at and next_run_at.
    """
    every_6h = cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=360)
    assert WD.cadence_seconds(every_6h) == 360 * MINUTE
    assert WD.threshold_seconds(WD.cadence_seconds(every_6h)) == 18 * 60 * MINUTE


def test_a_rearmed_job_cannot_raise_its_own_threshold_by_staying_broken() -> None:
    """The gap is only one cadence when measured off a COMPLETED run. A job
    re-armed without running leaves last_run_at stale while next_run_at advances,
    so the live gap reads as the whole outage. Deriving the threshold from that
    would make the guard QUIETER the longer the job stayed broken — an anti-guard.
    The cadence pinned on the last success is what must be used instead.
    """
    healthy = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=360)]
    state = settle(healthy)

    # 20h later: never ran again, but re-armed repeatedly. The live gap is now
    # ~26h, which alone would clamp the threshold to the 24h ceiling.
    drifted = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=1560, status="error")]
    assert WD.cadence_seconds(drifted[0]) == 1560 * MINUTE, "premise: the live gap is inflated"

    stale, _, _, _ = run(drifted, state, now=NOW + 20 * 60 * MINUTE)
    assert len(stale) == 1, (
        "20h of silence must page a 6h job at its pinned 18h threshold, not be "
        "deferred to 24h by a gap the outage itself inflated"
    )


def test_the_pinned_cadence_does_not_make_the_guard_trigger_happy() -> None:
    """The other direction: pinning must not shrink the threshold either. The
    same job at 17h of silence is still inside its 18h window.
    """
    healthy = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=360)]
    state = settle(healthy)
    drifted = [cron_job("triage", cadence_min=360, last_ago_min=0, next_in_min=1500, status="error")]
    stale, _, _, _ = run(drifted, state, now=NOW + 17 * 60 * MINUTE)
    assert stale == []


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


# --- More than one scheduler root --------------------------------------------


def test_a_stale_job_in_a_non_default_profile_fires() -> None:
    """The regression this section exists for. Reading only the default root
    leaves every profile-scoped job unwatched — and, worse, reports the fleet
    healthy while doing it, which is a guard that lies rather than one that is
    merely absent.
    """
    healthy = [job("audit", cadence_min=15, last_ok_ago_min=5)]
    state = settle([], profiles={"homelab-admin": healthy})
    stale, _, _, _ = run([], state, now=NOW + 175 * MINUTE, profiles={"homelab-admin": healthy})
    assert len(stale) == 1 and stale[0].startswith("homelab-admin/audit "), (
        "a stalled job outside the default root must page, and the message must "
        "name its profile or nobody can act on it"
    )


def test_same_named_jobs_in_two_profiles_do_not_collide() -> None:
    """Job names are unique only WITHIN a store. Keyed on the name alone, two
    profiles sharing one share a single state record: the healthy job keeps
    advancing the last-success clock, so the stalled one can never page.
    """
    both_healthy = {
        "homelab-admin": [job("digest", cadence_min=15, last_ok_ago_min=1)],
        "splunk-admin": [job("digest", cadence_min=15, last_ok_ago_min=1)],
    }
    state = settle([], profiles=both_healthy)
    stale, _, _, _ = run(
        [],
        state,
        now=NOW + 200 * MINUTE,
        profiles={
            # Still succeeding punctually, right up to the later tick.
            "homelab-admin": [job("digest", cadence_min=15, last_ok_ago_min=-199)],
            # Not since the baseline tick.
            "splunk-admin": [job("digest", cadence_min=15, last_ok_ago_min=1)],
        },
    )
    assert len(stale) == 1, f"exactly the stalled profile's job pages: {stale}"
    assert stale[0].startswith("splunk-admin/digest "), f"the wrong profile paged: {stale}"


def test_a_profile_without_a_job_store_is_skipped_silently(capsys) -> None:
    """A profile root that carries no store yet is a normal state, not an error.
    Reading one unconditionally would log a failure every tick forever — the
    kind of standing noise that trains an operator to ignore this guard.
    """
    healthy = [job("audit", cadence_min=15, last_ok_ago_min=5)]
    run([], profiles={"github-maint": None, "homelab-admin": healthy})
    lines = log_lines(capsys)
    assert not [line for line in lines if "unreadable" in line], (
        f"a storeless profile must not produce an error line: {lines}"
    )
    assert not [line for line in lines if "github-maint" in line], (
        f"a storeless profile must not be mentioned at all: {lines}"
    )
    assert any(line.startswith("homelab-admin/audit: ") for line in lines), (
        "a storeless profile must not stop the stores that do exist from being read"
    )


def test_healthy_jobs_across_every_store_stay_quiet() -> None:
    """The other direction across roots: reading more stores must not make the
    guard page on jobs that are fine.
    """
    profiles = {
        "homelab-admin": [job("audit", cadence_min=60, last_ok_ago_min=20)],
        "splunk-admin": [job("sweep", cadence_min=1440, last_ok_ago_min=300)],
        "github-maint": None,
    }
    default = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(default, profiles=profiles)
    stale, recovered, _, _ = run(default, state, profiles=profiles)
    assert stale == [] and recovered == []


# --- A deliberately stopped store --------------------------------------------
#
# A converge drains in-flight work before restarting, and an operator can stop a
# store by hand. Both leave a real gap in successful runs that exceeds this
# guard's floor. Paging for it would be the guard's own undoing: an alarm that
# fires during planned maintenance is one operators learn to dismiss, and the
# credibility spent on a false page is not recovered by a later true one.


def test_a_stalled_job_in_a_stopped_store_reports_paused_instead_of_paging(capsys) -> None:
    healthy = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    state = settle(healthy)
    stale, _, _, _ = run(healthy, state, now=NOW + 175 * MINUTE, paused=["default"])
    assert stale == [], "a store that deliberately stopped explains its own silence"
    assert any("PAUSED" in line for line in log_lines(capsys)), (
        "suppression must still be a logged decision, not a silent skip"
    )


def test_an_empty_stop_sentinel_still_counts_as_stopped() -> None:
    """Presence is the whole signal. A writer interrupted mid-write leaves an
    empty or truncated file, and deciding engagement from a body this guard does
    not own would fail open at exactly that moment.
    """
    run([])
    (TMP / "ESTOP").write_text("")
    try:
        assert [paused for _, paused, _ in WD.load_stores()] == [True]
    finally:
        (TMP / "ESTOP").unlink()


def test_stopping_one_store_does_not_silence_another() -> None:
    stale_job = [job("audit", cadence_min=15, last_ok_ago_min=5)]
    state = settle(stale_job, profiles={"homelab-admin": stale_job})
    stale, _, _, _ = run(
        stale_job,
        state,
        now=NOW + 175 * MINUTE,
        profiles={"homelab-admin": stale_job},
        paused=["default"],
    )
    assert len(stale) == 1 and stale[0].startswith("homelab-admin/audit "), (
        f"only the stopped store is suppressed: {stale}"
    )


def test_a_job_already_stale_before_the_pause_pages_as_soon_as_it_lifts() -> None:
    """The pause explains the paused interval and nothing else. A job that had
    already stopped succeeding before the stop went in is still broken after it
    lifts, and must page then rather than start over.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 200 * MINUTE, paused=["default"])
    stale, _, _, _ = run(jobs, state, now=NOW + 210 * MINUTE)
    assert len(stale) == 1, "200m of pre-pause silence does not stop being silence"


def test_a_healthy_job_is_not_charged_for_the_time_the_store_was_stopped() -> None:
    """The other direction, and the reason banking beats simply resuming the
    clock: a job that was fine going in must not page on the way out for a
    window it could not have run in. 45m stopped, 6m of its own silence.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    _, _, state, _ = run(jobs, state, now=NOW + 5 * MINUTE, paused=["default"])
    stale, _, _, _ = run(jobs, state, now=NOW + 50 * MINUTE)
    assert stale == [], "51m of wall clock minus 45m stopped is not a stall"


def test_repeated_pause_cycles_cannot_mask_a_job_that_has_stopped_succeeding() -> None:
    """Only real stopped time is discounted, and it accumulates honestly. A job
    dead the whole time still crosses its threshold on the wall clock the pauses
    do not cover — otherwise a cycling pause is a way to hide a dead fleet.

    Timed so the stall is still inside its 45m threshold at the last resume
    (41m of banked-adjusted silence) and crosses it 20m later. A guard that
    restarted the clock on resume would read 20m at that final tick and stay
    quiet; only the banked total exposes the real 61m of silence.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    for start, end in ((10, 20), (50, 60)):
        stale, _, state, _ = run(jobs, state, now=NOW + start * MINUTE, paused=["default"])
        assert stale == [], f"stopped at {start}m must be quiet"
        stale, _, state, _ = run(jobs, state, now=NOW + end * MINUTE)
        assert stale == [], f"resumed at {end}m is still inside the threshold"
    stale, _, _, _ = run(jobs, state, now=NOW + 80 * MINUTE)
    assert len(stale) == 1, "61m of silence with only 20m stopped is still a stall"


def test_a_store_left_stopped_indefinitely_raises_its_own_alert_once() -> None:
    """Suppression cannot be unbounded. Past the threshold the pause has stopped
    explaining the silence and has become the thing nobody has accounted for.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    _, _, state, opening = run(jobs, now=NOW, paused=["default"])
    assert opening == [], "a pause that just started is not yet a problem"
    _, _, state, late = run(jobs, state, now=NOW + 241 * MINUTE, paused=["default"])
    assert len(late) == 1 and late[0].startswith("default "), f"{late}"
    _, _, _, again = run(jobs, state, now=NOW + 300 * MINUTE, paused=["default"])
    assert again == [], "the pause alert must not become the noise it replaces"
