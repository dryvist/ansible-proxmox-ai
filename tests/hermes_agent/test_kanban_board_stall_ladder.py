"""Self-check for the stall ladder, the all-clear and the stuck-age line in
the master Kanban digest (kanban-digest.py.j2). Split from
test_kanban_board_alarms.py to stay under the token budget; the module
loader and fixtures come from there.

Runs bare (`python3 tests/hermes_agent/test_kanban_board_stall_ladder.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
from test_kanban_board_alarms import A_FINISHED_RUN, DIGEST, NO_RUNNING, NO_RUNS, NOW


def test_stall_alarm_follows_the_escalate_then_quiet_ladder_once_it_holds():
    """Vikunja 1853: the same line 427 ticks in a row. The streak keeps
    counting every tick; the LINE posts at the threshold, at three times it,
    then once every STALL_REPEAT_TICKS."""
    board = {"ready": 5, "running": 0}
    repeat = DIGEST.STALL_REPEAT_TICKS
    posted = []
    for prior in range(0, 3 * repeat):
        ticks, line = DIGEST.stall_alarm(board, NO_RUNS, NO_RUNNING, NOW, prior, threshold=3,
                                         max_in_progress=1)
        assert ticks == prior + 1, "the streak itself never stops counting"
        if line is not None:
            posted.append(ticks)
    assert posted == [3, 9, repeat, 2 * repeat, 3 * repeat]


def test_stall_alarm_posts_one_all_clear_when_a_held_alarm_clears():
    board = {"ready": 5, "running": 1}
    ticks, line = DIGEST.stall_alarm(board, A_FINISHED_RUN, NO_RUNNING, NOW, 9, threshold=3,
                                     max_in_progress=1)
    assert ticks == 0
    assert line is not None and "draining again" in line and "9 stalled" in line
    # And only when the alarm had actually fired — a streak below the
    # threshold clearing is not news.
    assert DIGEST.stall_alarm(board, A_FINISHED_RUN, NO_RUNNING, NOW, 2, threshold=3,
                              max_in_progress=1) == (0, None)


def test_stuck_query_ages_a_card_from_its_newest_touch():
    import sqlite3

    from _kanban_digest_shared import TASKS_DDL

    conn = sqlite3.connect(":memory:")
    conn.execute(TASKS_DDL)
    day = 86400
    rows = [
        # (id, status, created_at, started_at, last_heartbeat_at)
        ("old-blocked", "blocked", NOW - 9 * day, 0, 0),        # never ran: ages from creation
        ("heartbeat", "running", NOW - 9 * day, NOW - 5 * day, NOW - 1 * day),  # touched yesterday
        ("fresh", "ready", NOW - 1 * day, 0, 0),
        ("settled", "done", NOW - 30 * day, 0, 0),
    ]
    conn.executemany(
        "INSERT INTO tasks (id, title, status, created_at, started_at, last_heartbeat_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(i, i, st, c, s, h) for i, st, c, s, h in rows],
    )
    stuck = list(conn.execute(DIGEST.STUCK_SQL, (NOW - DIGEST.STUCK_HOURS * 3600,)))
    assert [(r[0], r[1]) for r in stuck] == [("blocked", 1)]
    assert stuck[0][2] == NOW - 9 * day


def test_stuck_line_names_counts_and_the_oldest_age_per_status():
    rows = [("blocked", 44, NOW - 9 * 86400), ("ready", 15, NOW - 3 * 86400)]
    line = DIGEST.stuck_line(rows, NOW)
    assert line.startswith(":hourglass: Stuck >48h:")
    assert "44 blocked, oldest 9d" in line
    assert "15 ready, oldest 3d" in line
    assert DIGEST.stuck_line([], NOW) is None


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
