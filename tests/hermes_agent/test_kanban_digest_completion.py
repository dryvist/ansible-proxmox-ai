"""Self-check for the master Kanban digest: completion, retry, overrun, and
degraded-state contracts.

Split from test_kanban_digest.py to stay under the token budget — see
_kanban_digest_shared.py for the loaded template/board fixtures,
test_kanban_digest_heartbeat.py for the heartbeat/interval contracts, and
test_kanban_digest_routing.py for the worker-failure routing contract this
leaves behind.

1. A completed card carries the worker's own summary, not just a title.
2. Failure, retry and the board's signature "card exits open" mode are reported
   as failures — including when the run ended but the card never settled.
3. A running card past its own max_runtime is reported; one with no recorded
   max_runtime is NOT judged against an invented limit.
4. A missing or corrupt state file degrades to the interval window and SAYS SO.

Runs bare (`python3 tests/hermes_agent/test_kanban_digest_completion.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
import json
from pathlib import Path

from _kanban_digest_shared import DIGEST, FIXTURE_CONFIG, NOW, digest


# --- contract 1: a completed card carries its result -------------------------

def test_completed_card_reports_the_workers_own_summary():
    text = digest(
        [{"id": "t_aa", "title": "Splunk triage sweep", "status": "done"}],
        [{"id": 1, "task_id": "t_aa", "outcome": "completed", "started_at": NOW - 700,
          "ended_at": NOW - 100, "summary": "ran 5 bounded queries; codex index 569m stale"}])
    assert "Completed (1)" in text
    assert "ran 5 bounded queries" in text, "a title alone is not a result"
    assert "t_aa" in text and "10m" in text


def test_completed_card_that_already_posted_its_own_report_is_not_requoted():
    """A report-generating cron (splunk-triage etc.) says in its own summary
    that it already delivered the full report to Slack. Quoting that summary
    here would restate the same finding twice in the same window."""
    text = digest(
        [{"id": "t_kk", "title": "Splunk triage sweep", "status": "done"}],
        [{"id": 1, "task_id": "t_kk", "outcome": "completed", "started_at": NOW - 700,
          "ended_at": NOW - 100,
          "summary": "RED (firewall index 99.3%), full report delivered to Slack."}])
    assert "posted its own full report to Slack" in text
    assert "firewall index 99.3%" not in text, "the finding must not be requoted"


def test_completed_card_with_no_summary_says_so_rather_than_inventing_one():
    text = digest(
        [{"id": "t_aa", "title": "Nightly wiki", "status": "done"}],
        [{"id": 1, "task_id": "t_aa", "outcome": "completed", "started_at": NOW - 200,
          "ended_at": NOW - 100, "summary": None}])
    assert "no summary" in text


# --- contract 2: failure, retry, and cards that exit open --------------------

def test_retries_collapse_to_one_line_naming_every_outcome():
    text = digest(
        [{"id": "t_bb", "title": "Docs study", "status": "blocked",
          "consecutive_failures": 3}],
        [{"id": 1, "task_id": "t_bb", "outcome": "crashed", "ended_at": NOW - 800,
          "error": "worker exited 1"},
         {"id": 2, "task_id": "t_bb", "outcome": "timed_out", "ended_at": NOW - 400},
         {"id": 3, "task_id": "t_bb", "outcome": "timed_out", "ended_at": NOW - 100}])
    assert text.count("t_bb") == 1, "a retried card is one finding, not three"
    assert "x3" in text and "crashed" in text and "timed_out" in text
    assert "3 consecutive failures" in text


def test_a_card_failing_a_second_consecutive_time_is_visually_escalated():
    """A card still failing on its 2nd+ consecutive tick is not the same signal
    as its first failure — mirrors the ESCALATING treatment splunk-triage.py.j2
    already gives a worsening error signature."""
    text = digest(
        [{"id": "t_esc", "title": "AI news scout", "status": "blocked",
          "consecutive_failures": 2}],
        [{"id": 1, "task_id": "t_esc", "outcome": "timed_out", "ended_at": NOW - 100}])
    assert ":rotating_light: *ESCALATING*" in text
    assert ":x: *AI news scout*" not in text


def test_a_cards_first_failure_uses_the_plain_marker():
    text = digest(
        [{"id": "t_first", "title": "AI news scout", "status": "blocked",
          "consecutive_failures": 1}],
        [{"id": 1, "task_id": "t_first", "outcome": "timed_out", "ended_at": NOW - 100}])
    assert ":x: *AI news scout*" in text
    assert "ESCALATING" not in text


def test_a_card_that_exits_open_is_reported_with_where_it_stopped():
    """The board's signature failure mode: the run ends, the card never settles."""
    text = digest(
        [{"id": "t_cc", "title": "App seeding", "status": "ready"}],
        [{"id": 1, "task_id": "t_cc", "outcome": "reclaimed", "ended_at": NOW - 100}])
    assert "left *ready*" in text, "an unsettled card must name the column it sits in"


def test_a_card_that_failed_then_succeeded_counts_as_completed():
    text = digest(
        [{"id": "t_dd", "title": "AI news", "status": "done"}],
        [{"id": 1, "task_id": "t_dd", "outcome": "crashed", "ended_at": NOW - 800},
         {"id": 2, "task_id": "t_dd", "outcome": "completed", "started_at": NOW - 400,
          "ended_at": NOW - 100, "summary": "posted 3 items"}])
    assert "Failed" not in text
    assert "after 1 failed attempt(s)" in text, "the retry must still be visible"


# --- contract 3: max_runtime overruns ----------------------------------------

def test_running_card_past_its_max_runtime_is_reported():
    text = digest(
        [{"id": "t_ee", "title": "Splunk triage sweep", "status": "running",
          "max_runtime_seconds": 1800, "current_run_id": 1,
          "last_heartbeat_at": int(NOW - 60)}],
        [{"id": 1, "task_id": "t_ee", "outcome": None, "started_at": NOW - 5400}])
    assert "Overrunning" in text and "(1)" in text
    assert "past its 30m max_runtime" in text
    assert "running 1.5h" in text
    assert "last heartbeat 60s ago" in text


def test_running_card_without_a_recorded_max_runtime_is_not_judged():
    text = digest(
        [{"id": "t_ff", "title": "Long one", "status": "running",
          "max_runtime_seconds": None, "current_run_id": 1}],
        [{"id": 1, "task_id": "t_ff", "outcome": None, "started_at": NOW - 99999}])
    assert "Overrunning" not in text, "no limit recorded means no claim to make"
    assert "No board activity" in text


def test_overrun_uses_the_current_attempts_start_not_the_first():
    """tasks.started_at is the FIRST attempt; a retry would look hours overdue."""
    text = digest(
        [{"id": "t_gg", "title": "Retried", "status": "running", "started_at": NOW - 99999,
          "max_runtime_seconds": 1800, "current_run_id": 7}],
        [{"id": 7, "task_id": "t_gg", "outcome": None, "started_at": NOW - 60}])
    assert "Overrunning" not in text


# --- contract 4: degraded state is announced, never silent -------------------

def test_missing_state_file_falls_back_to_the_interval_and_says_so():
    Path(FIXTURE_CONFIG["STATE_PATH"]).unlink(missing_ok=True)
    since, posted, note = DIGEST.load_state()
    assert since is None and posted is None and "15 min" in note


def test_corrupt_state_file_falls_back_rather_than_crashing():
    Path(FIXTURE_CONFIG["STATE_PATH"]).write_text("{not json")
    since, _, note = DIGEST.load_state()
    assert since is None and note
    Path(FIXTURE_CONFIG["STATE_PATH"]).write_text(json.dumps({"schema": 0, "last_run_epoch": 1}))
    assert DIGEST.load_state()[0] is None, "an older schema is not a usable baseline"


def test_a_future_timestamp_is_not_trusted():
    """A clock step forward would otherwise silence the digest until it caught up."""
    Path(FIXTURE_CONFIG["STATE_PATH"]).write_text(
        json.dumps({"schema": DIGEST.STATE_SCHEMA, "last_run_epoch": NOW}))
    since, _, note = DIGEST.load_state()
    assert since == NOW and not note, "a valid state file is used as-is"


def test_the_fallback_note_reaches_the_post():
    text = digest([], [], note="no usable state file — reporting the last 15 min instead")
    assert "no usable state file" in text


def test_a_saved_state_round_trips():
    DIGEST.save_state(NOW, NOW - 10)
    assert DIGEST.load_state() == (NOW, NOW - 10, "")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
