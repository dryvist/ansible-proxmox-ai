"""Self-check for the master Kanban digest: worker failures route away from
the work log.

Split from test_kanban_digest.py to stay under the token budget — see
_kanban_digest_shared.py for the loaded template/board fixtures,
test_kanban_digest_completion.py for the completion/retry/overrun/degraded-
state contracts, and test_kanban_digest_heartbeat.py for the heartbeat/
interval contracts this leaves behind.

These lines are always Hermes' own workers dying (turn-budget exhaustion,
judge timeouts, max_runtime overruns), never board activity. Over a 13-day
audit they were the single largest category in the work channel.

Runs bare (`python3 tests/hermes_agent/test_kanban_digest_routing.py`) or
under pytest. Plain asserts, no fixtures, no framework.
"""
from _kanban_digest_shared import DIGEST, NOW, digest, digest_split

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
