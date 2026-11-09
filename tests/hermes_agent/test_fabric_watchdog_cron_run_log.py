"""fabric_watchdog makes every Hermes cron run COUNTABLE from the logs.

The absence-of-success guard logs a decision per job per TICK, which answers
"is it still succeeding" but not "did a run happen at all" — a failed run is
visible only as an alert, and a quiet healthy tick is indistinguishable from a
job that never fired. The `cron-run` line is the per-run record: exactly one
line per run the store holds that was never reported before, labelled by
(profile, job), so runs can be counted straight out of journald.

`delivered` is `unknown` on every line by construction: the job store holds
WHERE a job delivers (its `deliver` field) but records no delivery outcome, so
a yes/no would claim a fact nobody observed.

Lives under tests/hermes_agent/ beside the other fabric_watchdog contracts,
which share this template's harness (_fabric_watchdog_shared).
"""

from __future__ import annotations

from _fabric_watchdog_shared import (
    MINUTE,
    NOW,
    WD,
    cron_run_lines,
    job,
    run,
)


def test_one_new_run_prints_exactly_one_line(capsys) -> None:
    """One observed run, one line — per profile, with every field the contract
    names. A store with no `deliver` field reports `target=none`.
    """
    digest = job("digest", cadence_min=15, last_ok_ago_min=5, deliver="slack:C0123")
    sweep = job("sweep", cadence_min=60, last_ok_ago_min=20)
    state = {"schema": WD.STATE_SCHEMA, "jobs": {}, "stores": {}}
    run([digest], state, profiles={"homelab-admin": [sweep]})

    lines = cron_run_lines(capsys)
    assert len(lines) == 2, f"each newly observed run prints exactly one line: {lines}"
    digest_line = next(line for line in lines if "job=digest " in line)
    sweep_line = next(line for line in lines if "job=sweep " in line)
    assert digest_line.startswith("cron-run profile=default job=digest ")
    assert sweep_line.startswith("cron-run profile=homelab-admin job=sweep ")
    for line, record in ((digest_line, digest), (sweep_line, sweep)):
        assert f"status={record['last_status']} " in line
        assert f"run_at={record['last_run_at']} " in line
        assert "delivered=unknown " in line, "the store records no delivery outcome"
    assert digest_line.endswith("target=slack:C0123")
    assert sweep_line.endswith("target=none")


def test_an_unchanged_store_prints_nothing(capsys) -> None:
    """The exactly-once rule's quiet half: a run already reported is never
    re-printed on a later tick that observes the same store.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    _, _, state, _ = run(jobs)
    cron_run_lines(capsys)  # discard the first tick, which reports the observed run
    run(jobs, state)
    assert cron_run_lines(capsys) == []


def test_a_restart_with_the_memory_file_prints_nothing_new(capsys) -> None:
    """The high-water mark lives in the memory FILE, not in the process: saved
    and reloaded the way main() writes and a fresh process reads it, a restart
    replays nothing the previous process already reported.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    _, _, state, _ = run(jobs)
    cron_run_lines(capsys)  # discard the first tick
    WD.save_state(state)
    restarted = WD.load_state()
    run(jobs, restarted)
    assert cron_run_lines(capsys) == []


def test_a_run_landing_during_a_missed_tick_is_counted_once(capsys) -> None:
    """The loud half of the same rule: a newer run observed after ticks were
    missed still prints exactly once — not skipped, not doubled.
    """
    jobs = [job("digest", cadence_min=15, last_ok_ago_min=5)]
    _, _, state, _ = run(jobs)
    assert cron_run_lines(capsys), "premise: the baseline run was reported"
    later = [job("digest", cadence_min=15, last_ok_ago_min=-10)]
    run(later, state, now=NOW + 20 * MINUTE)  # a cadence and a half passed unobserved
    lines = cron_run_lines(capsys)
    assert len(lines) == 1, f"a missed tick must not skip the newest run: {lines}"
    run(later, state, now=NOW + 21 * MINUTE)
    assert cron_run_lines(capsys) == []
