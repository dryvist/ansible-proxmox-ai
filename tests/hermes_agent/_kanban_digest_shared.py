"""Shared fixtures for the master Kanban digest self-checks (roles/hermes_agent).

The digest reports what the board did since its own previous run, reading
kanban.db read-only. Loads the deployed kanban-digest.py.j2 template ONCE and
provides a throwaway kanban.db builder, so every split test module exercises
the same rendered artifact and the same shipped SQL Ansible would ship.

The schema is fixed against the live kanban.db observed on the guest, so a
Hermes upgrade that renames a column fails here rather than in Slack.
"""
import re
import sqlite3
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/kanban-digest.py.j2"
DEFAULTS_PATH = REPO_ROOT / "roles" / "hermes_agent"

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
  max_runtime_seconds INTEGER, last_heartbeat_at INTEGER, current_run_id INTEGER,
  assignee TEXT
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
