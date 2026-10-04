"""Self-check for the master Kanban digest's silent quiet-run contract.

Split from test_kanban_digest.py to stay under the token budget — see
_kanban_digest_shared.py for the loaded template/board fixtures,
test_kanban_digest_completion.py for the completion/retry/overrun/degraded-
state contracts, and test_kanban_digest_routing.py for the worker-failure
routing contract this leaves behind.

- Quiet runs never post a heartbeat.
- Genuinely nothing happened prints an explicit line naming the board, never
  an empty post.
- The schedule and the script's fallback window come from the ONE interval
  variable, and the digest channels are never literal Slack ids.
- A broken database is delivered as a failure, not silence.

Runs bare (`python3 tests/hermes_agent/test_kanban_digest_heartbeat.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
import contextlib
import io
import re

from _role_files import role_defaults
from _kanban_digest_shared import (
    DEFAULTS_PATH,
    DIGEST,
    FIXTURE_CONFIG,
    NOW,
    TEMPLATE_PATH,
    TMP,
    digest,
    heartbeat,
    load_digest_module,
)
from pathlib import Path


# --- never post a message whose whole content is "nothing happened" ----------

def test_a_quiet_run_is_silent_when_the_heartbeat_has_not_elapsed():
    """The defect this fixes: a 15-minute cron posting ~90 identical
    "No board activity" messages a day, each repeating the same board counts."""
    assert digest([{"id": "t_q", "title": "Waiting", "status": "ready"}], [],
                  due=False) == DIGEST.SILENT


def test_a_quiet_run_never_posts_a_heartbeat():
    tasks = [{"id": "t_q", "title": "Waiting", "status": "ready"}]
    assert digest(tasks, [], due=True) == DIGEST.SILENT
    assert heartbeat(tasks, [], due=True) == ""


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


def test_heartbeat_is_disabled_for_unknown_or_stale_state():
    assert not DIGEST.heartbeat_due(NOW, None)
    assert not DIGEST.heartbeat_due(NOW, NOW - 86400)
    assert not DIGEST.heartbeat_due(NOW, NOW + 99999)
    assert not DIGEST.heartbeat_due(NOW, NOW - 60), "one minute ago is not due"


def test_a_suppressed_run_advances_the_window_but_not_the_last_post():
    """Otherwise the next real activity is double-reported, or the heartbeat
    never fires because every quiet run resets its own clock."""
    from _kanban_digest_shared import board

    Path(FIXTURE_CONFIG["STATE_PATH"]).unlink(missing_ok=True)
    board([{"id": "t_u", "title": "Waiting", "status": "ready"}], [])
    mod = load_digest_module()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert mod.main() == 0
    assert "no usable state file" in buf.getvalue(), "a degraded read remains visible"
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
    defaults = role_defaults(DEFAULTS_PATH)
    assert defaults["hermes_agent_kanban_digest_heartbeat_hours"] == 0
    source = TEMPLATE_PATH.read_text()
    assert "HEARTBEAT_HOURS = {{ hermes_agent_kanban_digest_heartbeat_hours }}" in source
    assert re.search(r"^\s*HEARTBEAT_HOURS\s*=\s*\d", source, re.M) is None


# --- nothing happened is stated, not implied ----------------------------------

def test_quiet_run_names_the_board_rather_than_posting_nothing():
    tasks = [{"id": "t_hh", "title": "Waiting", "status": "ready"}]
    assert digest(tasks, []) == DIGEST.SILENT
    assert heartbeat(tasks, []) == ""


def test_runs_outside_the_window_are_not_reported():
    task = {"id": "t_ii", "title": "Old", "status": "done"}
    runs = [{"id": 1, "task_id": "t_ii", "outcome": "completed", "ended_at": NOW - 4000,
             "summary": "old news"}]
    assert "old news" not in digest([task], runs)
    assert heartbeat([task], runs) == ""


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


# --- one interval variable, no drift ------------------------------------------

def test_schedule_and_fallback_window_come_from_the_one_interval_variable():
    defaults = role_defaults(DEFAULTS_PATH)
    interval = defaults["hermes_agent_kanban_digest_interval_minutes"]
    assert interval == 60, "steady-state cadence is hourly; the soak cadence (15) is over"
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
    defaults = role_defaults(DEFAULTS_PATH)
    for var in ("hermes_agent_kanban_digest_channel",
                "hermes_agent_kanban_digest_issues_channel",
                "hermes_agent_kanban_digest_noise_channel"):
        channel = defaults[var]
        assert "{{" in channel, f"{var} must stay a Jinja expression"
        assert not re.search(r"\bC0[A-Z0-9]{8,}\b", channel), \
            f"{var} carries a literal Slack channel id"

    # The work log follows the agent home channel; separate healthy posts are
    # disabled, so no noise destination receives heartbeats.
    assert "hermes_agent_slack_hermes_all_channel" in \
        defaults["hermes_agent_kanban_digest_channel"]
    assert "hermes_agent_slack_issues_channel" in \
        defaults["hermes_agent_kanban_digest_issues_channel"]
    assert "hermes_agent_slack_noise_channel" in \
        defaults["hermes_agent_kanban_digest_noise_channel"]


def test_a_broken_database_is_delivered_as_a_failure_not_as_silence():
    """A schema change must announce itself; an empty post would read as healthy."""
    broken = load_digest_module({**FIXTURE_CONFIG, "DB_PATH": str(TMP / "gone.db")})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert broken.main() == 0, "the cron must always exit 0 so stdout is delivered"
    assert "FAILED" in buf.getvalue()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
