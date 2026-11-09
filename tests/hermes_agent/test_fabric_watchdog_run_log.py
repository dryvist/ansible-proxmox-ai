"""fabric_watchdog run-line contract: every cron run countable from stdout.

The absence-of-success alert needs a human reading a channel; this output is
the machine-countable one — one `cron-run` line per newly observed run, keyed
by (profile, job) and carrying the fields the store already holds, so
journald alone answers "how often did this job actually fire".

`delivered` is always `unknown` by construction: the store holds delivery
targets (`deliver`) but no delivery outcome — that lives in the scheduler's
run ledger, which is not this store. Asserting `unknown` here pins that
honesty: a future edit that starts guessing yes/no from a run's `last_status`
would conflate "the job ran" with "anything was delivered".

The dedup watermark lives in the shared per-job memory, so the three ways a
line could go wrong are pinned: a new run prints once, an unchanged store
prints nothing, and a restart that reloads the memory file prints nothing new.
"""

from __future__ import annotations

import re

from _fabric_watchdog_shared import (
    MINUTE,
    NOW,
    WD,
    job,
    run,
    settle,
)


def cron_run_fields(raw: str):
    """The `cron-run` lines in one captured stdout blob, key=value parsed.

    Read from real stdout, like log_lines(): the contract is a line in
    journald, and a stubbed printer would prove a call happened rather than
    that anything was emitted. The parsed form keeps field assertions
    readable; a missing field fails loudly here instead of arriving silently
    unparseable downstream.
    """
    lines = [line for line in raw.splitlines() if line.startswith("cron-run ")]
    parsed = []
    for line in lines:
        fields = dict(field.split("=", 1) for field in line[len("cron-run "):].split(" "))
        assert len(fields) == 6, f"run line is not six key=value fields: {line}"
        parsed.append(fields)
    return parsed


def cron_run_lines(capsys):
    """What the tick printed, drained once so decision lines and run lines
    are read from the same capture."""
    return cron_run_fields(capsys.readouterr().out)


def test_one_new_run_prints_exactly_one_line(capsys) -> None:
    """A tick that observes a run it has never reported prints one line for
    it; the next tick, having reported it, prints nothing more."""
    state = settle([job("digest", cadence_min=15, last_ok_ago_min=5, deliver="slack:C0TEST")])
    first = cron_run_lines(capsys)
    assert len(first) == 1, f"the first observed run must print once: {first}"

    # The store's newest run advanced: one clean run since the last tick.
    newest = job("digest", cadence_min=15, last_ok_ago_min=-199, deliver="slack:C0TEST")
    _, _, state, _ = run([newest], state, now=NOW + 200 * MINUTE)
    second = cron_run_lines(capsys)
    assert len(second) == 1, f"a new run must print exactly one line: {second}"
    assert second[0] == {
        "profile": "default",
        "job": "digest",
        "status": "ok",
        "run_at": newest["last_run_at"],
        "delivered": "unknown",
        "target": "slack:C0TEST",
    }

    _, _, state, _ = run([newest], state, now=NOW + 215 * MINUTE)
    assert cron_run_lines(capsys) == [], "the reported run must not print again on the next tick"


def test_an_unchanged_store_prints_nothing(capsys) -> None:
    """The watermark moves only when the store's run does. A fleet ticking
    along unchanged must add zero cron-run lines per tick — otherwise the
    count tracks ticks, not runs."""
    jobs = [
        job("watchdog-fast", cadence_min=15, last_ok_ago_min=5),
        job("hourly", cadence_min=60, last_ok_ago_min=20),
    ]
    state = settle(jobs)
    cron_run_lines(capsys)  # drain the first-sighting lines from the baseline tick
    for tick in range(1, 4):
        _, _, state, _ = run(jobs, state, now=NOW + tick * MINUTE)
        assert cron_run_lines(capsys) == [], f"tick {tick} reprinted a run the memory already reported"


def test_a_restart_with_the_memory_file_present_prints_nothing_new(capsys) -> None:
    """The watermark is in the saved memory, not in the process. Round-trip
    the state through the real state file — save_state then load_state, the
    same calls a restarted service makes — and the same store must print
    nothing; asserting against shared in-memory dicts would pass here while
    every real restart re-announced the fleet's last runs."""
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    _, _, state, _ = run(jobs)
    assert len(cron_run_lines(capsys)) == 1, "premise: the first tick reports the run"

    WD.save_state(state)
    reloaded = WD.load_state()
    assert reloaded["jobs"]["default/digest"]["last_reported_run"] is not None, (
        "the watermark must survive the save, or every restart reprints"
    )
    _, _, _, _ = run(jobs, reloaded)
    assert cron_run_lines(capsys) == [], "a restart that reloads the memory file must print nothing new"


def test_a_run_that_landed_while_the_watchdog_was_down_is_counted_once(capsys) -> None:
    """A tick missed entirely must neither skip nor duplicate: comparing
    against the newest run REPORTED (not the last tick seen) is what makes a
    gap self-healing. Two ticks' worth of time passes between observations;
    the store shows only the newest run, so it is reported once, at the next
    tick, and never again."""
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    _, _, state, _ = run(jobs)
    cron_run_lines(capsys)
    gap_run = job("digest", cadence_min=15, last_ok_ago_min=-29, status="error")
    _, _, state, _ = run([gap_run], state, now=NOW + 30 * MINUTE)
    lines = cron_run_lines(capsys)
    assert len(lines) == 1 and lines[0]["run_at"] == gap_run["last_run_at"], (
        f"the run from the gap must print exactly once: {lines}"
    )
    assert lines[0]["status"] == "error", "a failed run is still a run that happened"
    _, _, state, _ = run([gap_run], state, now=NOW + 45 * MINUTE)
    assert cron_run_lines(capsys) == []


def test_the_line_reports_only_fields_the_store_holds(capsys) -> None:
    """No delivery outcome exists in this store, so `delivered` is `unknown`
    however the run ended, and `target` comes from the job's own `deliver`
    field or reads `none` when the job carries none."""
    delivered_style = job("poster", cadence_min=15, last_ok_ago_min=1, deliver="slack:C0TEST")
    bare = job("bare", cadence_min=15, last_ok_ago_min=1)
    failed = job("failing", cadence_min=15, last_ok_ago_min=1, status="error", deliver="local")

    _, _, _, _ = run([delivered_style, bare, failed])
    parsed = cron_run_lines(capsys)
    by_job = {fields["job"]: fields for fields in parsed}
    assert len(by_job) == 3, f"one line per job: {parsed}"
    assert by_job["poster"]["delivered"] == "unknown"
    assert by_job["poster"]["target"] == "slack:C0TEST"
    assert by_job["failing"]["status"] == "error"
    assert by_job["failing"]["delivered"] == "unknown", (
        "the store records no delivery outcome for a failed run either"
    )
    assert by_job["failing"]["target"] == "local"
    assert by_job["bare"]["delivered"] == "unknown"
    assert by_job["bare"]["target"] == "none"


def test_a_profile_job_is_labelled_with_its_profile(capsys) -> None:
    """Job names are unique only WITHIN a store, so the line must carry the
    profile for the count to split per root."""
    profile_job = [job("audit", cadence_min=15, last_ok_ago_min=5, deliver="slack:C0PROFILE")]
    _, _, _, _ = run([], profiles={"homelab-admin": profile_job})
    parsed = cron_run_lines(capsys)
    assert len(parsed) == 1 and parsed[0]["profile"] == "homelab-admin"
    assert parsed[0]["job"] == "audit"


def test_the_alert_path_is_untouched_by_the_run_lines() -> None:
    """Counting runs must not change what pages: the same stalled fleet that
    alerted before this output existed must still alert identically, and a
    failing job's new runs keep being counted while the stall stays latched."""
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=1)]
    state = settle(jobs)
    stale, recovered, state, _ = run(jobs, state, now=NOW + 200 * MINUTE)
    assert len(stale) == 1 and stale[0].startswith("default/digest ") and recovered == []
    failing = [job("digest", cadence_min=15, last_ok_ago_min=-199, status="error")]
    stale, _, _, _ = run(failing, state, now=NOW + 205 * MINUTE)
    assert stale == [], "an already-alerted stall must stay latched"


def test_disabled_jobs_keep_their_runs_counted_and_their_watermark(capsys) -> None:
    """A run that happened is countable whatever the job's state is now —
    the line sits before the skip paths — so a run that landed while the
    watchdog was down and the job was disabled since still prints once, and
    re-enabling later prints nothing for it again."""
    enabled = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    _, _, state, _ = run(enabled)
    cron_run_lines(capsys)
    # Disabled since the watchdog last looked, but its newest run is newer
    # than the one already reported: still exactly one line.
    last_run_then_disabled = [job("digest", cadence_min=15, last_ok_ago_min=-7, enabled=False)]
    _, _, state, _ = run(last_run_then_disabled, state, now=NOW + 8 * MINUTE)
    lines = cron_run_lines(capsys)
    assert len(lines) == 1 and lines[0]["job"] == "digest", f"{lines}"
    _, _, state, _ = run(enabled, state, now=NOW + 15 * MINUTE)
    assert cron_run_lines(capsys) == [], "re-enabling must not reprint the reported run"


def test_log_decision_lines_are_never_mistaken_for_run_lines(capsys) -> None:
    """The `cron-run` prefix is the countable contract; the per-job decision
    log keeps its own prefix. Both appear on one tick, and only the former
    parses as run lines. One capture serves both, since readouterr() drains."""
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    _, _, _, _ = run(jobs)
    raw = capsys.readouterr().out
    decisions = [
        line[len("cron-success-watchdog: "):]
        for line in raw.splitlines()
        if line.startswith("cron-success-watchdog: ")
    ]
    assert any(line.startswith("default/digest: ") for line in decisions), (
        "the per-job decision log must keep its own prefix"
    )
    parsed = cron_run_fields(raw)
    assert len(parsed) == 1 and parsed[0]["job"] == "digest"
    rendered = "cron-run " + " ".join(f"{k}={v}" for k, v in parsed[0].items())
    assert re.match(
        r"^cron-run profile=\S+ job=\S+ status=\S+ run_at=\S+ delivered=\S+ target=\S+$", rendered
    )
