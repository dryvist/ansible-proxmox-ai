"""Self-check for the master Kanban digest (roles/hermes_agent).

The digest reports what the board did since its own previous run, reading
kanban.db read-only. The contracts checked here are the ones that would fail
SILENTLY in production — a digest that says "no board activity" while cards are
crash-looping is worse than no digest at all:

1. A completed card carries the worker's own summary, not just a title.
2. Failure, retry and the board's signature "card exits open" mode are reported
   as failures — including when the run ended but the card never settled.
3. A running card past its own max_runtime is reported; one with no recorded
   max_runtime is NOT judged against an invented limit.
4. A missing or corrupt state file degrades to the interval window and SAYS SO.
5. Genuinely nothing happened prints an explicit line naming the board, never
   an empty post.
6. The schedule and the script's fallback window come from the ONE interval
   variable.

The schema is fixed against the live kanban.db observed on the guest, so a
Hermes upgrade that renames a column fails here rather than in Slack.

Runs bare (`python3 tests/hermes_agent/test_kanban_digest.py`) or under pytest.
Plain asserts, no fixtures, no framework.
"""
import json
import re
import sqlite3
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/kanban-digest.py.j2"
DEFAULTS_PATH = REPO_ROOT / "roles/hermes_agent/defaults/main.yml"

TMP = Path(tempfile.mkdtemp(prefix="kanban-digest-selfcheck-"))
# Stand-ins for the values Ansible renders from defaults/main.yml.
FIXTURE_CONFIG = {
    "DB_PATH": str(TMP / "kanban.db"),
    # No file at this path by default: read_max_in_progress() must degrade to
    # None, never raise, when config.yaml has not been deployed yet.
    "CONFIG_PATH": str(TMP / "config.yaml"),
    "STATE_PATH": str(TMP / "kanban-digest.json"),
    "TITLE": "Kanban Board Digest",
    "INTERVAL_MIN": 15,
    "HEARTBEAT_HOURS": 6,
    "ISSUES_CHANNEL": "C_ISSUES",
    # Deliberately absent: send_to_issues must report failure, not raise, so the
    # caller can fall back to inlining the failure lines.
    "HERMES_BIN": str(TMP / "no-such-hermes-binary"),
    "ISSUES_MARKER": "[ISSUES]",
    "STALL_TICKS_THRESHOLD": 3,
    "TIMEOUT_RATE_THRESHOLD": 3,
    "BLOCKED_GROWTH_THRESHOLD": 3,
}

# Columns as they exist on the live board. Only what the digest reads.
TASKS_DDL = """
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, title TEXT, status TEXT, started_at INTEGER,
  completed_at INTEGER, consecutive_failures INTEGER, max_retries INTEGER,
  max_runtime_seconds INTEGER, last_heartbeat_at INTEGER, current_run_id INTEGER
)
"""
RUNS_DDL = """
CREATE TABLE task_runs (
  id INTEGER PRIMARY KEY, task_id TEXT, status TEXT, started_at INTEGER,
  ended_at INTEGER, outcome TEXT, summary TEXT, error TEXT
)
"""
NOW = 1785000000.0


def load_digest_module(config=None):
    """Render the template's config lines to fixtures and import it as a module."""
    config = config or FIXTURE_CONFIG
    out = []
    for line in TEMPLATE_PATH.read_text().splitlines():
        if "ansible_managed" in line:
            continue
        match = re.match(r"^(\w+) = .*\{\{", line)
        if match:
            name = match.group(1)
            assert name in config, f"template config {name} has no self-check fixture"
            out.append(f"{name} = {config[name]!r}")
            continue
        out.append(line)
    rendered = "\n".join(out)
    assert "{{" not in rendered, "self-check left an unrendered Jinja expression"
    mod = types.ModuleType("kanban_digest")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


DIGEST = load_digest_module()


def board(tasks, runs):
    """A throwaway kanban.db with the given rows; returns the query results.

    Goes through real SQLite rather than hand-built row dicts so the shipped SQL
    — the part that breaks on a schema change — is what gets exercised.
    """
    path = TMP / "kanban.db"
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(TASKS_DDL + ";" + RUNS_DDL)
    conn.executemany(
        "INSERT INTO tasks (id,title,status,started_at,completed_at,consecutive_failures,"
        "max_retries,max_runtime_seconds,last_heartbeat_at,current_run_id) "
        "VALUES (:id,:title,:status,:started_at,:completed_at,:consecutive_failures,"
        ":max_retries,:max_runtime_seconds,:last_heartbeat_at,:current_run_id)",
        [{"started_at": None, "completed_at": None, "consecutive_failures": 0,
          "max_retries": 2, "max_runtime_seconds": None, "last_heartbeat_at": None,
          "current_run_id": None, **t} for t in tasks])
    conn.executemany(
        "INSERT INTO task_runs (id,task_id,status,started_at,ended_at,outcome,summary,error) "
        "VALUES (:id,:task_id,:status,:started_at,:ended_at,:outcome,:summary,:error)",
        [{"status": r.get("outcome"), "started_at": None, "ended_at": None,
          "summary": None, "error": None, **r} for r in runs])
    conn.commit()
    conn.close()

    read = DIGEST.connect(str(path))
    try:
        return (list(read.execute(DIGEST.RUNS_SQL, (NOW - 900, NOW))),
                list(read.execute(DIGEST.RUNNING_SQL)),
                {row[0]: row[1] for row in read.execute(DIGEST.BOARD_SQL)})
    finally:
        read.close()


def now_dt():
    import datetime as dt
    return dt.datetime.fromtimestamp(NOW, dt.timezone.utc)


def digest(tasks, runs, since=NOW - 900, note="", due=True):
    """The work-log post only — what #hermes-all receives."""
    text, _ = DIGEST.build_digest(*board(tasks, runs), now_dt(), since, note, due)
    return text


def digest_split(tasks, runs, since=NOW - 900, note="", due=True):
    """(work_log_text, issues_text) with failure routing enabled."""
    return DIGEST.build_digest(*board(tasks, runs), now_dt(), since, note, due,
                               split_failures=True)


# --- contract 1: a completed card carries its result -------------------------

def test_completed_card_reports_the_workers_own_summary():
    text = digest(
        [{"id": "t_aa", "title": "Splunk triage sweep", "status": "done"}],
        [{"id": 1, "task_id": "t_aa", "outcome": "completed", "started_at": NOW - 700,
          "ended_at": NOW - 100, "summary": "ran 5 bounded queries; codex index 569m stale"}])
    assert "Completed (1)" in text
    assert "ran 5 bounded queries" in text, "a title alone is not a result"
    assert "t_aa" in text and "10m" in text


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


# --- contract 7: never post a message whose whole content is "nothing happened"

def test_a_quiet_run_is_silent_when_the_heartbeat_has_not_elapsed():
    """The defect this fixes: a 15-minute cron posting ~90 identical
    "No board activity" messages a day, each repeating the same board counts."""
    assert digest([{"id": "t_q", "title": "Waiting", "status": "ready"}], [],
                  due=False) == DIGEST.SILENT


def test_a_quiet_run_still_posts_once_the_heartbeat_elapses():
    text = digest([{"id": "t_q", "title": "Waiting", "status": "ready"}], [], due=True)
    assert "No board activity" in text
    assert "heartbeat" in text, "the heartbeat post must say why it is rare"


def test_real_board_activity_is_never_heartbeat_suppressed():
    """The gate covers ONLY the quiet branch. A failure, a completion or an
    overrun is the reason the digest exists and must post immediately."""
    failed = digest(
        [{"id": "t_r", "title": "Docs study", "status": "blocked"}],
        [{"id": 1, "task_id": "t_r", "outcome": "crashed", "ended_at": NOW - 100,
          "error": "worker exited 1"}], due=False)
    assert failed != DIGEST.SILENT and "Docs study" in failed

    completed = digest(
        [{"id": "t_s", "title": "AI news", "status": "done"}],
        [{"id": 1, "task_id": "t_s", "outcome": "completed", "started_at": NOW - 200,
          "ended_at": NOW - 100, "summary": "posted 3 items"}], due=False)
    assert completed != DIGEST.SILENT and "posted 3 items" in completed

    overrun = digest(
        [{"id": "t_t", "title": "Sweep", "status": "running",
          "max_runtime_seconds": 1800, "current_run_id": 1}],
        [{"id": 1, "task_id": "t_t", "outcome": None, "started_at": NOW - 5400}], due=False)
    assert overrun != DIGEST.SILENT and "Overrunning" in overrun


def test_heartbeat_is_due_when_the_last_post_is_unknown_or_stale():
    """Erring towards posting is the only safe direction — a suppressed heartbeat
    is indistinguishable from a dead cron, which is what this digest announces."""
    assert DIGEST.heartbeat_due(NOW, None), "unknown last post must post"
    assert DIGEST.heartbeat_due(NOW, NOW - DIGEST.HEARTBEAT_HOURS * 3600), "exactly due"
    assert DIGEST.heartbeat_due(NOW, NOW + 99999), "a future last-post is a clock step"
    assert not DIGEST.heartbeat_due(NOW, NOW - 60), "one minute ago is not due"


def test_a_suppressed_run_advances_the_window_but_not_the_last_post():
    """Otherwise the next real activity is double-reported, or the heartbeat
    never fires because every quiet run resets its own clock."""
    import io
    import contextlib

    Path(FIXTURE_CONFIG["STATE_PATH"]).unlink(missing_ok=True)
    board([{"id": "t_u", "title": "Waiting", "status": "ready"}], [])
    mod = load_digest_module()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mod.main() == 0
    assert buf.getvalue().strip() != mod.SILENT, "no state file means the heartbeat is due"
    first_run, first_post, _ = mod.load_state()
    assert first_post is not None

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mod.main() == 0
    assert buf.getvalue().strip() == mod.SILENT, "the second quiet run is suppressed"
    second_run, second_post, _ = mod.load_state()
    assert second_run > first_run, "the window must always advance"
    assert second_post == first_post, "a suppressed run must not reset the heartbeat"


def test_the_heartbeat_ceiling_is_configurable_not_a_literal():
    import yaml

    defaults = yaml.safe_load(DEFAULTS_PATH.read_text())
    assert defaults["hermes_agent_kanban_digest_heartbeat_hours"] == 6
    source = TEMPLATE_PATH.read_text()
    assert "HEARTBEAT_HOURS = {{ hermes_agent_kanban_digest_heartbeat_hours }}" in source
    assert re.search(r"^\s*HEARTBEAT_HOURS\s*=\s*\d", source, re.M) is None


# --- contract 5: nothing happened is stated, not implied ---------------------

def test_quiet_run_names_the_board_rather_than_posting_nothing():
    text = digest([{"id": "t_hh", "title": "Waiting", "status": "ready"}], [])
    assert "No board activity" in text
    assert "1 ready" in text, "the quiet line must name what it searched"
    assert text.splitlines()[0].startswith("*Kanban Board Digest*")


def test_runs_outside_the_window_are_not_reported():
    text = digest(
        [{"id": "t_ii", "title": "Old", "status": "done"}],
        [{"id": 1, "task_id": "t_ii", "outcome": "completed", "ended_at": NOW - 4000,
          "summary": "old news"}])
    assert "old news" not in text and "No board activity" in text


def test_a_section_over_the_cap_says_how_many_it_hid():
    n = DIGEST.MAX_ITEMS + 3
    text = digest(
        [{"id": f"t_{i:02d}", "title": f"Card {i}", "status": "done"} for i in range(n)],
        [{"id": i, "task_id": f"t_{i:02d}", "outcome": "completed", "started_at": NOW - 200,
          "ended_at": NOW - 100, "summary": "ok"} for i in range(n)])
    assert f"Completed ({n})" in text
    assert "and 3 more not shown" in text


def test_a_long_summary_is_clipped_with_a_visible_marker():
    text = digest(
        [{"id": "t_jj", "title": "Verbose", "status": "done"}],
        [{"id": 1, "task_id": "t_jj", "outcome": "completed", "started_at": NOW - 200,
          "ended_at": NOW - 100, "summary": "x" * 5000}])
    assert "…" in text and len(max(text.splitlines(), key=len)) < 600


# --- contract 6: one interval variable, no drift ------------------------------

def test_schedule_and_fallback_window_come_from_the_one_interval_variable():
    import yaml

    defaults = yaml.safe_load(DEFAULTS_PATH.read_text())
    interval = defaults["hermes_agent_kanban_digest_interval_minutes"]
    assert interval == 15, "soak cadence; steady state is hourly (60)"
    schedule = defaults["hermes_agent_kanban_digest_cron_schedule"]
    assert "hermes_agent_kanban_digest_interval_minutes" in schedule, \
        "the schedule must be derived from the interval variable, not hand-written"
    assert f"*/{interval}" not in schedule, "the interval must not be restated as a literal"
    # The script's fallback window is the same Jinja expression, so the rendered
    # source must carry no second literal.
    source = TEMPLATE_PATH.read_text()
    assert "INTERVAL_MIN = {{ hermes_agent_kanban_digest_interval_minutes }}" in source
    assert re.search(r"^\s*INTERVAL_MIN\s*=\s*\d", source, re.M) is None


def test_the_digest_channels_are_never_literal_ids():
    """Both destinations stay env-fed expressions — an id in git is the defect."""
    import yaml

    defaults = yaml.safe_load(DEFAULTS_PATH.read_text())
    for var in ("hermes_agent_kanban_digest_channel",
                "hermes_agent_kanban_digest_issues_channel"):
        channel = defaults[var]
        assert "{{" in channel, f"{var} must stay a Jinja expression"
        assert not re.search(r"\bC0[A-Z0-9]{8,}\b", channel), \
            f"{var} carries a literal Slack channel id"

    # The work log is #hermes-all, not the shared digest surface: that alias is
    # what collapsed every tier onto one channel.
    assert "hermes_agent_slack_hermes_all_channel" in \
        defaults["hermes_agent_kanban_digest_channel"]
    assert "hermes_agent_slack_issues_channel" in \
        defaults["hermes_agent_kanban_digest_issues_channel"]


def test_a_broken_database_is_delivered_as_a_failure_not_as_silence(capsys=None):
    """A schema change must announce itself; an empty post would read as healthy."""
    import io
    import contextlib

    broken = load_digest_module({**FIXTURE_CONFIG, "DB_PATH": str(TMP / "gone.db")})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert broken.main() == 0, "the cron must always exit 0 so stdout is delivered"
    assert "FAILED" in buf.getvalue()


# --- contract 7: worker failures route away from the work log ----------------
# These lines are always Hermes' own workers dying (turn-budget exhaustion,
# judge timeouts, max_runtime overruns), never board activity. Over a 13-day
# audit they were the single largest category in the work channel.

FAILING_CARD = [{"id": "t_ff", "title": "Splunk triage sweep", "status": "blocked"}]
FAILING_RUN = [{"id": 1, "task_id": "t_ff", "outcome": "failed", "started_at": NOW - 600,
                "ended_at": NOW - 60,
                "error": "Goal-mode worker exhausted its turn budget (8/8)"}]


def test_worker_failures_leave_the_work_log_and_carry_their_reason():
    text, issues = digest_split(FAILING_CARD, FAILING_RUN)
    assert "Failed / retried / left open" in issues
    assert "exhausted its turn budget" in issues, \
        "the routed post must carry the diagnosis, not just the card name"
    assert "t_ff" in issues
    assert "Failed / retried / left open" not in text, \
        "a worker failure must not also sit in the work log"


def test_a_run_of_only_failures_is_quiet_for_the_work_log_not_a_bare_header():
    """The peel happens before the heartbeat gate, so nothing posts a lone header."""
    text, issues = digest_split(FAILING_CARD, FAILING_RUN, due=False)
    assert text == DIGEST.SILENT
    assert "exhausted its turn budget" in issues, "the failure must survive the gate"


def test_completions_stay_in_the_work_log_while_failures_leave():
    text, issues = digest_split(
        FAILING_CARD + [{"id": "t_ok", "title": "Nightly wiki", "status": "done"}],
        FAILING_RUN + [{"id": 2, "task_id": "t_ok", "outcome": "completed",
                        "started_at": NOW - 300, "ended_at": NOW - 30,
                        "summary": "wiki rebuilt, 12 pages"}])
    assert "Completed (1)" in text and "wiki rebuilt" in text
    assert "exhausted its turn budget" in issues
    assert "wiki rebuilt" not in issues


def test_split_is_off_by_default_so_an_unconfigured_channel_changes_nothing():
    text = digest(FAILING_CARD, FAILING_RUN)
    assert "Failed / retried / left open" in text
    assert "exhausted its turn budget" in text


def test_a_failed_send_reports_failure_rather_than_raising():
    """Fail-soft is the whole safety property: the caller inlines on False.

    HERMES_BIN points at a path that does not exist, so this exercises the real
    except path — a dropped failure report is worse than a misrouted one.
    """
    assert DIGEST.send_to_issues("anything") is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
