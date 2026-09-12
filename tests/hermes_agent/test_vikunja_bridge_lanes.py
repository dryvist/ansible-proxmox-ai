"""Contract checks for the Vikunja bridge's needs_triage and busy lanes.

Split from test_vikunja_bridge.py to stay under the repo's per-file token
budget — see _vikunja_bridge_shared.py for the loaded template/fixtures this
shares with that file.

Three properties worth pinning here:

1. A task does not age silently in Blocked. Dwell time is tracked from first
   sighting and the task moves to Needs Triage once it crosses
   blocked_triage_days — the mechanism behind Vikunja 1523's 398 aged blocked
   cards silently starving 12 ready ones.
2. A card that failed while the shared brain was saturated is not confused
   with a card that failed because the work was bad — it goes to Busy and is
   re-queued, never Blocked.
3. Intake claims nothing while the watchdog's last probe was not "up" — the
   capacity gate has no second probe of its own to disagree with the one the
   watchdog already runs.
"""
import os
import sqlite3
import time
from pathlib import Path

from _vikunja_bridge_shared import BRIDGE, _patch


def test_read_probe_state_reads_the_watchdogs_persisted_file_and_fails_open():
    path = Path(BRIDGE.PROBE_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("busy\n")
    assert BRIDGE.read_probe_state() == "busy"
    path.write_text("garbage\n")
    assert BRIDGE.read_probe_state() == "up", "unrecognized content fails open"
    path.unlink()
    assert BRIDGE.read_probe_state() == "up", "a guest with no watchdog must not starve intake"


def test_triage_aged_blocked_starts_the_clock_then_moves_after_the_threshold():
    calls = []
    board = {"pid": 1, "view": 1, "blocked": 10, "needs_triage": 11,
             "buckets": [{"id": 10, "title": "Blocked", "tasks": [{"id": 30}, {"id": 31}]}]}
    with _patch(move_to_bucket=lambda p, v, b, t: calls.append(("move", b, t)),
                comment=lambda t, text: calls.append(("comment", t))):
        # First sighting: the clock starts, nothing moves yet.
        blocked_since = {}
        assert BRIDGE.triage_aged_blocked(board, blocked_since) == 0
        assert set(blocked_since) == {"30", "31"}
        assert calls == []

        # 30 aged past the threshold; 31 is still fresh.
        blocked_since["30"] = time.time() - (BRIDGE.BLOCKED_TRIAGE_DAYS * 86400 + 10)
        moved = BRIDGE.triage_aged_blocked(board, blocked_since)
        assert moved == 1
        assert ("move", 11, 30) in calls
        assert "30" not in blocked_since, "a triaged task must stop being tracked"
        assert "31" in blocked_since, "an untriaged task keeps its dwell clock"

    # A task that left Blocked on its own must stop being tracked too.
    board["buckets"][0]["tasks"] = [{"id": 31}]
    assert BRIDGE.bucket_tasks(board, 10) == [{"id": 31}]


def test_requeue_busy_waits_for_retry_after_then_moves_to_ready():
    calls = []
    board = {"pid": 1, "view": 1, "ready": 2}
    with _patch(move_to_bucket=lambda p, v, b, t: calls.append(("move", b, t)),
                comment=lambda t, text: calls.append(("comment", t))):
        not_yet = {"1": time.time() + 100}
        assert BRIDGE.requeue_busy(board, not_yet) == 0
        assert calls == [], "a retry-after in the future must not requeue yet"

        due = {"2": time.time() - 1}
        assert BRIDGE.requeue_busy(board, due) == 1
        assert due == {}, "a requeued task must be dropped from the busy ledger"
        assert ("move", 2, 2) in calls


def test_reconcile_routes_a_probe_busy_failure_to_busy_not_blocked():
    conn = sqlite3.connect(BRIDGE.DB_PATH)
    conn.executescript(
        "CREATE TABLE tasks (id TEXT, status TEXT, consecutive_failures INT,"
        " max_retries INT);"
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT,"
        " outcome TEXT, summary TEXT, error TEXT, ended_at REAL);")
    conn.execute("INSERT INTO tasks VALUES ('card-99', 'failed', 1, 2)")
    conn.execute("INSERT INTO task_runs VALUES (1, 'card-99', 'error', NULL, 'timeout', 5)")
    conn.commit()
    conn.close()
    try:
        calls = []
        board = {"pid": 1, "view": 1, "blocked": 10, "busy": 12}
        tracked = {"77": "card-99"}
        busy_tasks = {}
        with _patch(move_to_bucket=lambda p, v, b, t: calls.append(("move", b, t)),
                    comment=lambda t, text: calls.append(("comment", t)),
                    read_probe_state=lambda: "busy"):
            settled = BRIDGE.reconcile(board, tracked, busy_tasks)
        assert settled == 1
        assert "77" not in tracked, "a busy-routed card is no longer awaited by the ledger"
        assert "77" in busy_tasks and busy_tasks["77"] > time.time()
        assert ("move", 12, 77) in calls, "must move to Busy, not Blocked"
        assert ("move", 10, 77) not in calls
    finally:
        os.remove(BRIDGE.DB_PATH)


def test_hard_dep_is_withheld_while_any_parenttask_relation_is_undone():
    responses = {
        1: {"related_tasks": {"parenttask": [{"done": True}]}},
        2: {"related_tasks": {"parenttask": [{"done": True}, {"done": False}]}},
        3: {"related_tasks": {}},
    }

    def fake_api(method, path, body=None):
        return responses[int(path.rsplit("/", 1)[-1])]

    with _patch(api=fake_api):
        assert BRIDGE.has_undone_parent(1) is False
        assert BRIDGE.has_undone_parent(2) is True
        assert BRIDGE.has_undone_parent(3) is False, "no parenttask relation blocks nothing"

    hard_dep = [{"title": BRIDGE.INTAKE_LABEL}, {"title": BRIDGE.HARD_DEP_LABEL}]
    with _patch(api=fake_api):
        assert BRIDGE.actionable({"id": 1, "title": "t", "labels": hard_dep}, {})
        assert not BRIDGE.actionable({"id": 2, "title": "t", "labels": hard_dep}, {})
        assert BRIDGE.actionable({"id": 3, "title": "t", "labels": hard_dep}, {})
    # A task without the label is never routed through has_undone_parent at
    # all — no relation lookup for the common (non-hard-dep) case.
    assert BRIDGE.actionable(
        {"id": 2, "title": "t", "labels": [{"title": BRIDGE.INTAKE_LABEL}]}, {})


def test_intake_is_gated_on_the_watchdog_probe_state():
    calls = []
    board_stub = {"pid": 1, "view": 1, "ready": 2, "in_progress": None, "done": None,
                  "blocked": None, "needs_triage": None, "busy": None, "buckets": []}
    common = dict(
        resolve_board=lambda: board_stub,
        reconcile=lambda *a: 0,
        requeue_busy=lambda *a: 0,
        triage_aged_blocked=lambda *a: 0,
        intake=lambda *a: calls.append("intake") or 0,
    )
    with _patch(read_probe_state=lambda: "busy", **common):
        BRIDGE.tick()
    assert calls == [], "intake must not run while the probe is not up"

    with _patch(read_probe_state=lambda: "up", **common):
        BRIDGE.tick()
    assert calls == ["intake"], "intake must run once the probe reads up"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
