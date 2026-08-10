"""Self-check for the three board-health alarms in the master Kanban digest
(kanban-digest.py.j2): stalled board, task-timeout rate, blocked-task growth.

The gap these close: a production incident ran for hours while every health
signal stayed green — a queue can sit full with zero workers running, tasks
can time out repeatedly, and blocked tasks can pile up, all silently. These
alarms route to the issues channel via the digest's existing send_to_issues()
path, never the work log, and are never heartbeat-suppressed.

Contract 4 is the honesty one: the gateway's own dispatcher warning ("0
workers spawned. Check profile health...") has asserted a cause the data does
not establish — it fired on a configured max_in_progress cap and cost an
operator an hour of misdirected investigation. The stall alarm must never
repeat that mistake.

Runs bare (`python3 tests/hermes_agent/test_kanban_board_alarms.py`) or under
pytest. Plain asserts, no fixtures, no framework.
"""
import importlib.util
import re
import tempfile
from pathlib import Path
from _role_files import role_defaults

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/kanban-digest.py.j2"
DEFAULTS_PATH = REPO_ROOT / "roles" / "hermes_agent"

TMP = Path(tempfile.mkdtemp(prefix="kanban-board-alarms-selfcheck-"))
FIXTURE_CONFIG = {
    "DB_PATH": str(TMP / "kanban.db"),
    "CONFIG_PATH": str(TMP / "config.yaml"),
    "STATE_PATH": str(TMP / "kanban-digest.json"),
    "TITLE": "Kanban Board Digest",
    "INTERVAL_MIN": 15,
    "HEARTBEAT_HOURS": 6,
    "ISSUES_CHANNEL": "C_ISSUES",
    "HERMES_BIN": str(TMP / "no-such-hermes-binary"),
    "ISSUES_MARKER": "[ISSUES]",
    "STALL_TICKS_THRESHOLD": 3,
    "TIMEOUT_RATE_THRESHOLD": 3,
    "BLOCKED_GROWTH_THRESHOLD": 3,
}


def load_digest_module():
    """Render the template's config lines to fixtures and load it as a module.

    Same mechanism test_kanban_digest.py uses: any new `NAME = ... {{ }}`
    constant the template gains must show up here too, or this assertion is
    exactly what catches the drift. The rendered source is written to a real
    .py file and loaded via importlib — the same mechanics that run the
    deployed script, not a string-eval shortcut.
    """
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
    rendered_path = TMP / "kanban_digest_rendered.py"
    rendered_path.write_text(rendered)
    spec = importlib.util.spec_from_file_location("kanban_board_alarms", rendered_path)
    assert spec is not None and spec.loader is not None, \
        "rendered template did not resolve to a loadable module"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DIGEST = load_digest_module()


# --- alarm (a): stalled board — ready work, nothing running ------------------

NOW = 1785000000.0
NO_RUNS = []
A_FINISHED_RUN = [{"outcome": "completed"}]
NO_RUNNING = []


def test_stall_alarm_does_not_fire_before_the_threshold():
    board = {"ready": 2, "running": 0}
    ticks, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 0, threshold=3, max_in_progress=1)
    assert (ticks, line) == (1, None)
    ticks, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 1, threshold=3, max_in_progress=1)
    assert (ticks, line) == (2, None), "a single quiet tick must never page"


def test_stall_alarm_fires_once_the_streak_reaches_the_threshold():
    board = {"ready": 2, "running": 0}
    ticks, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 2, threshold=3, max_in_progress=1)
    assert ticks == 3
    assert line is not None
    assert "3 consecutive digest ticks" in line
    assert "2 ready card(s)" in line


def test_stall_alarm_repeats_every_tick_once_it_holds():
    """Like overrun_lines(), a stalled board is not stale news."""
    board = {"ready": 5, "running": 0}
    ticks, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 6, threshold=3, max_in_progress=1)
    assert ticks == 7 and line is not None


def test_stall_alarm_fires_on_a_run_wedged_at_the_cap_even_though_something_is_running():
    """The bug this alarm exists to fix: at max_in_progress=1, a single wedged
    run shows running=1 forever, so "running == 0" would never trigger. The
    real incident had ready=17, running=1, zero finishes, for hours."""
    board = {"ready": 17, "running": 1}
    ticks, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 2, threshold=3, max_in_progress=1)
    assert ticks == 3 and line is not None
    assert "17 ready card(s)" in line and "1 running but nothing finished" in line


def test_stall_alarm_resets_when_any_run_finishes_even_with_ready_work_left():
    board = {"ready": 5, "running": 1}
    assert DIGEST.stall_alarm(board, A_FINISHED_RUN, NO_RUNNING, NOW, 9, threshold=3,
                              max_in_progress=1) == (0, None)


def test_stall_alarm_resets_when_the_ready_queue_is_empty():
    board = {"ready": 0, "running": 0}
    assert DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 9, threshold=3,
                              max_in_progress=1) == (0, None)


def test_stall_alarm_with_a_readable_cap_states_the_fact_not_a_spawn_failure():
    """The exact defect this alarm exists to not repeat: the gateway's own
    warning blamed venv/PATH/credentials when the real cause was a configured
    max_in_progress=1 cap. This alarm must never make that same leap."""
    board = {"ready": 3, "running": 0}
    _, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 2, threshold=3, max_in_progress=1)
    assert "in_progress 0/1 (cap)" in line
    for misleading in ("spawn fail", "check profile health", "venv", "credentials", "PATH"):
        assert misleading.lower() not in line.lower(), f"must not imply {misleading!r}"


def test_stall_alarm_readable_cap_reports_the_actual_running_count_not_zero():
    """The real incident's shape: running=1 at cap=1, nothing finishing."""
    board = {"ready": 17, "running": 1}
    _, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 2, threshold=3, max_in_progress=1)
    assert "in_progress 1/1 (cap)" in line


def test_stall_alarm_with_an_unreadable_cap_names_the_live_possibilities():
    board = {"ready": 3, "running": 0}
    _, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 2, threshold=3, max_in_progress=None)
    assert "undetermined" in line
    assert "max_in_progress" in line and "spawn failure" in line, \
        "an unreadable cap must name live possibilities, not assert one"


def test_stall_alarm_reports_the_oldest_running_occupants_age():
    """The number that separates "legitimately long task" from "wedged" at a
    glance. Reuses RUNNING_SQL's rows (already fetched for overrun_lines) and
    fmt_dur() — no new query, no new formatting."""
    board = {"ready": 17, "running": 1}
    running_rows = [{"run_started": NOW - 5400}]  # 1.5h ago
    _, line = DIGEST.stall_alarm(board, NO_RUNS, running_rows, NOW, 2, threshold=3, max_in_progress=1)
    assert "oldest running 1.5h" in line


def test_stall_alarm_omits_the_age_note_when_no_running_rows_are_available():
    board = {"ready": 5, "running": 0}
    _, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, 2, threshold=3, max_in_progress=1)
    assert "oldest running" not in line


# --- alarm (b): task-timeout rate ---------------------------------------------

def _run(outcome):
    return {"outcome": outcome}


def test_timeout_alarm_does_not_fire_below_the_threshold():
    rows = [_run("timed_out"), _run("timed_out"), _run("completed")]
    assert DIGEST.timeout_alarm(rows, threshold=3) is None


def test_timeout_alarm_fires_at_the_threshold():
    rows = [_run("timed_out")] * 3 + [_run("completed")]
    line = DIGEST.timeout_alarm(rows, threshold=3)
    assert line is not None and "3 task run(s) timed out" in line


def test_timeout_alarm_only_counts_the_timed_out_outcome():
    rows = [_run("crashed")] * 5 + [_run("timed_out")] * 2
    assert DIGEST.timeout_alarm(rows, threshold=3) is None, \
        "crashed runs must not inflate the timeout count"


# --- alarm (c): blocked-task growth -------------------------------------------

def test_blocked_growth_alarm_does_not_fire_on_a_static_backlog():
    """A long-standing blocked card must not page forever."""
    count, line = DIGEST.blocked_growth_alarm({"blocked": 40}, prior_blocked=40, threshold=3)
    assert (count, line) == (40, None)


def test_blocked_growth_alarm_fires_on_a_rising_delta():
    count, line = DIGEST.blocked_growth_alarm({"blocked": 44}, prior_blocked=40, threshold=3)
    assert count == 44
    assert line is not None and "grew by 4" in line


def test_blocked_growth_alarm_does_not_fire_on_a_shrinking_count():
    count, line = DIGEST.blocked_growth_alarm({"blocked": 10}, prior_blocked=40, threshold=3)
    assert (count, line) == (10, None)


def test_blocked_growth_alarm_always_returns_the_current_count_as_the_new_baseline():
    """The caller persists this every tick regardless of whether the alarm
    fired — otherwise the baseline itself would drift."""
    count, _ = DIGEST.blocked_growth_alarm({}, prior_blocked=5, threshold=3)
    assert count == 0, "an absent 'blocked' bucket means zero blocked cards, not unknown"


# --- alarm cap source: read_max_in_progress() ---------------------------------

def test_read_max_in_progress_returns_none_when_config_is_absent():
    Path(DIGEST.CONFIG_PATH).unlink(missing_ok=True)
    assert DIGEST.read_max_in_progress() is None, \
        "no config.yaml deployed yet must degrade to unknown, not raise"


def test_read_max_in_progress_parses_the_rendered_config_line():
    path = Path(DIGEST.CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("kanban:\n  dispatch_in_gateway: true\n  max_in_progress: 2\n"
                    "  max_in_progress_per_profile: 2\n")
    try:
        assert DIGEST.read_max_in_progress() == 2
    finally:
        path.unlink()


def test_read_max_in_progress_is_not_fooled_by_the_per_profile_sibling_key():
    """config.yaml.j2 renders both keys adjacent, with the SAME leading digits
    possible for each (e.g. both 1). The anchored `max_in_progress:` pattern
    must bind to the sum-cap key even when the per-profile key differs, and
    regardless of which key line comes first."""
    path = Path(DIGEST.CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text("kanban:\n  max_in_progress: 1\n  max_in_progress_per_profile: 9\n")
        assert DIGEST.read_max_in_progress() == 1, "must not match the _per_profile line"

        path.write_text("kanban:\n  max_in_progress_per_profile: 9\n  max_in_progress: 1\n")
        assert DIGEST.read_max_in_progress() == 1, "must hold with the sibling key listed first"
    finally:
        path.unlink()


# --- wiring: alarms route to the issues channel, never the work log ----------

def test_extra_issue_lines_land_in_issues_text_not_the_work_log():
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    text, issues = DIGEST.build_digest(
        [], [], {"ready": 1}, now, now.timestamp() - 900, "",
        due=True, extra_issue_lines=["a stall alarm line"])
    assert "board alarms" in issues and "a stall alarm line" in issues
    assert "a stall alarm line" not in text


def test_extra_issue_lines_survive_heartbeat_suppression():
    """These alarms are never subject to the "nothing happened" quiet gate —
    the whole point is that they fire while every other signal stays quiet."""
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    text, issues = DIGEST.build_digest(
        [], [], {"ready": 1}, now, now.timestamp() - 900, "",
        due=False, extra_issue_lines=["a stall alarm line"])
    assert text == DIGEST.SILENT
    assert "a stall alarm line" in issues


def test_no_alarms_means_no_issues_block_added():
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    _, issues = DIGEST.build_digest(
        [], [], {}, now, now.timestamp() - 900, "", due=True, extra_issue_lines=[])
    assert issues == ""


# --- thresholds are configurable, not literals --------------------------------

def test_thresholds_are_role_variables_not_hardcoded():
    import yaml

    defaults = role_defaults(DEFAULTS_PATH)
    for var in ("hermes_agent_kanban_digest_stall_ticks_threshold",
                "hermes_agent_kanban_digest_timeout_threshold",
                "hermes_agent_kanban_digest_blocked_growth_threshold"):
        assert isinstance(defaults[var], int) and defaults[var] > 0

    source = TEMPLATE_PATH.read_text()
    assert "STALL_TICKS_THRESHOLD = {{ hermes_agent_kanban_digest_stall_ticks_threshold }}" in source
    assert "TIMEOUT_RATE_THRESHOLD = {{ hermes_agent_kanban_digest_timeout_threshold }}" in source
    assert "BLOCKED_GROWTH_THRESHOLD = {{ hermes_agent_kanban_digest_blocked_growth_threshold }}" in source


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
