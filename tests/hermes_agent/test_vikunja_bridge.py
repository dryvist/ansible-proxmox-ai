"""Contract checks for the Vikunja <-> Hermes Kanban bridge.

The bridge writes to the operator's real board, so the properties worth pinning
are the ones whose violation is expensive and INVISIBLE:

1. It never reports a result it does not have. A running card, an unknown card,
   and a card that is `done` with no completed run must all read as "not
   settled" rather than as success — a false "completed" comment on the
   operator's board is worse than no comment.
2. It never picks up a task it was not given. Every gate in `actionable` is a
   reason NOT to touch a task.
3. Its writes go through the endpoints that actually work. A bucket move is a
   dedicated route; sending `bucket_id` in a task update is a silent no-op that
   would leave the board frozen while every comment still landed correctly —
   the hardest possible failure to notice.
4. The token never reaches the world-readable script.

Runs bare (`python3 tests/hermes_agent/test_vikunja_bridge.py`) or under pytest.
Plain asserts, no fixtures — same shape as the rest of this suite.
"""
import re
import sqlite3
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
from _role_files import role_defaults, role_tasks_text

ROLE = REPO_ROOT / "roles/hermes_agent"
TEMPLATE_PATH = ROLE / "templates/vikunja-bridge.py.j2"
TEMPLATE = TEMPLATE_PATH.read_text()
SERVICE = (ROLE / "templates/hermes-vikunja-bridge.service.j2").read_text()
ENV_TEMPLATE = (ROLE / "templates/hermes-vikunja-bridge.env.j2").read_text()
TASKS = role_tasks_text(ROLE)
DEFAULTS_PATH = ROLE

STATE_DIR = tempfile.mkdtemp(prefix="vikunja-bridge-selfcheck-")
# Stand-ins for what Ansible renders from roles/hermes_agent/defaults/main.yml.
FIXTURE_CONFIG = {
    "HERMES_BIN": "/usr/local/bin/hermes",
    "HERMES_HOME": STATE_DIR,
    "DB_PATH": str(Path(STATE_DIR) / "kanban.db"),
    "STATE_PATH": str(Path(STATE_DIR) / "state/vikunja-bridge.json"),
    "VIKUNJA_URL": "https://vikunja.example.invalid",
    "PROJECT_NAME": "Hermes",
    "AGENT_NAME": "Hermes",
    "BUCKET_READY": "Ready",
    "BUCKET_IN_PROGRESS": "In Progress",
    "BUCKET_DONE": "Done",
    "BUCKET_BLOCKED": "Blocked",
    "POLL_INTERVAL": 60,
    "CARD_MAX_RUNTIME": "45m",
    "CARD_MAX_RETRIES": 2,
    "CARD_ASSIGNEE": "",
    "INTAKE_LABEL": "hermes",
    "MAX_INTAKE_PER_TICK": 3,
}


def load_bridge_module():
    """Render the template's config lines to fixtures and import it as a module."""
    out = []
    for line in TEMPLATE.splitlines():
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
    mod = types.ModuleType("vikunja_bridge")
    exec(compile(rendered, str(TEMPLATE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


BRIDGE = load_bridge_module()


def board_db():
    """An in-memory stand-in with kanban.db's real column shape."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE tasks (id TEXT, status TEXT, consecutive_failures INT,"
        " max_retries INT);"
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT,"
        " outcome TEXT, summary TEXT, error TEXT, ended_at REAL);")
    return conn


def test_the_scripts_own_self_check_passes():
    """The template ships `--self-check`; running it here means the assertions
    inside the deployed artifact are exercised by CI, not just by an operator
    who remembers to run them on the guest."""
    BRIDGE._self_check()


def test_an_unfinished_card_is_never_reported_as_a_result():
    conn = board_db()
    conn.execute("INSERT INTO tasks VALUES ('c1', 'running', 0, 2)")
    conn.execute("INSERT INTO tasks VALUES ('c2', 'queued', 0, 2)")
    for card in ("c1", "c2"):
        assert BRIDGE.card_state(conn, card) == (False, False, ""), card
    # A card the board no longer knows about must WAIT, not settle. Treating an
    # unknown id as failure would spam the operator's board on any schema drift.
    assert BRIDGE.card_state(conn, "gone") == (False, False, "")


def test_done_without_a_completed_run_is_not_success():
    """The status column and the run outcome must BOTH agree before this posts
    a success. Either one alone has been wrong on this board before."""
    conn = board_db()
    conn.execute("INSERT INTO tasks VALUES ('c1', 'done', 0, 2)")
    settled, ok, _ = BRIDGE.card_state(conn, "c1")
    assert (settled, ok) == (True, False), "done with no run row must not read as success"

    conn.execute("INSERT INTO tasks VALUES ('c2', 'done', 0, 2)")
    conn.execute("INSERT INTO task_runs VALUES (1, 'c2', 'error', NULL, 'boom', 5)")
    settled, ok, text = BRIDGE.card_state(conn, "c2")
    assert (settled, ok) == (True, False) and "boom" in text


def test_a_failure_comment_carries_the_evidence_not_just_a_verdict():
    conn = board_db()
    conn.execute("INSERT INTO tasks VALUES ('c1', 'failed', 2, 2)")
    conn.execute("INSERT INTO task_runs VALUES (1, 'c1', 'error', NULL, 'stack trace', 9)")
    _, ok, text = BRIDGE.card_state(conn, "c1")
    assert not ok
    for expected in ("status=failed", "outcome=error", "attempts=2/2", "stack trace"):
        assert expected in text, f"{expected!r} missing from {text!r}"


def test_only_the_latest_finished_run_decides_the_outcome():
    """A retry that eventually succeeded must not be reported as the earlier
    failure, and vice versa — the card's verdict is its LAST finished attempt."""
    conn = board_db()
    conn.execute("INSERT INTO tasks VALUES ('c1', 'done', 1, 2)")
    conn.execute("INSERT INTO task_runs VALUES (1, 'c1', 'error', NULL, 'first try', 10)")
    conn.execute("INSERT INTO task_runs VALUES (2, 'c1', 'completed', 'second try', NULL, 20)")
    settled, ok, text = BRIDGE.card_state(conn, "c1")
    assert (settled, ok, text) == (True, True, "second try")


def test_intake_gates_exclude_everything_not_explicitly_offered():
    tracked = {"5": "card-5"}
    labelled = [{"title": BRIDGE.INTAKE_LABEL}]
    rejected = [
        {"id": 1, "done": True, "title": "t", "labels": labelled},
        {"id": 5, "title": "t", "labels": labelled},            # already bridged
        {"id": 2, "title": "   ", "description": "", "labels": labelled},
        {"id": 6, "title": "unlabelled work"},                  # no opt-in label
        {"id": 7, "title": "wrong label", "labels": [{"title": "other"}]},
    ]
    for task in rejected:
        assert not BRIDGE.actionable(task, tracked), task
    assert BRIDGE.actionable({"id": 8, "title": "real work", "labels": labelled}, tracked)


def test_bucket_moves_use_the_dedicated_route_with_post():
    """Two live-verified facts (2026-07-26), each a silent failure if broken.

    ROUTE: Vikunja does not move a task between buckets on a task update — it
    only auto-moves on a `done` flip. Sending bucket_id in an update succeeds
    and moves nothing, freezing the board while every comment still lands.

    METHOD: PUT on this route returns 405 on the running instance, despite
    upstream's client docs saying PUT. POST is what actually works.
    """
    assert "/views/{view}/buckets/{bucket}/tasks" in TEMPLATE, (
        "the bucket move must go through the dedicated bucket-tasks route")
    move = TEMPLATE.split("def move_to_bucket")[1].split("\ndef ")[0]
    # Code only — the docstring names the wrong calls in order to warn against
    # them, and must not be read as the function making them.
    code = move.split('"""')[-1]
    assert '"POST"' in code and '"task_id": task_id' in code, code
    assert '"PUT"' not in code, "PUT returns 405 on the deployed API — POST is the verb"
    assert "bucket_id" not in code, (
        "bucket_id in a task update is the silent-no-op version of this call")


def test_tasks_are_read_from_the_view_tasks_route_not_the_buckets_route():
    """Live-verified 2026-07-26: `/views/{v}/buckets` returns buckets carrying
    only a `count` — no `tasks` key at all — while `/views/{v}/tasks` returns
    the buckets WITH their tasks.

    Reading tasks from `/buckets` therefore yields an empty list for every
    bucket, forever, with a 200 and nothing in any log. The daemon would run
    flawlessly and never pick up a single task — the easiest way to silently
    break the whole bridge, so it is pinned.
    """
    fetch = TEMPLATE.split("def view_buckets")[1].split("\ndef ")[0]
    code = fetch.split('"""')[-1]
    assert "/views/{view}/tasks" in code, code
    called = code.split("api(")[1].split(")")[0]
    assert "buckets" not in called, (
        "the /buckets route carries no tasks — reading it makes intake a silent no-op")

    # ready_tasks must consume that already-fetched list, not re-query.
    ready = TEMPLATE.split("def ready_tasks")[1].split("\ndef ")[0]
    assert "api(" not in ready, "ready_tasks must not issue its own request"
    assert 'board["buckets"]' in ready and 'entry.get("tasks")' in ready, ready


def test_ready_tasks_reads_the_right_bucket_out_of_the_board():
    board = {"ready": 2, "buckets": [
        {"id": 1, "title": "Backlog", "tasks": [{"id": 10}]},
        {"id": 2, "title": "Ready", "tasks": [{"id": 11}, {"id": 12}]},
        {"id": 3, "title": "Done", "tasks": [{"id": 13}]},
    ]}
    assert [t["id"] for t in BRIDGE.ready_tasks(board)] == [11, 12]
    # A bucket returned with no tasks key at all must read as empty, not
    # explode — that is exactly the shape the /buckets route returns.
    assert BRIDGE.ready_tasks({"ready": 9, "buckets": [{"id": 9, "title": "Ready"}]}) == []
    assert BRIDGE.ready_tasks({"ready": 99, "buckets": []}) == []


def test_the_write_token_never_reaches_the_script_template():
    """The script is 0750; the env file is 0600. A token templated into the
    former would be readable by anything in the hermes group and would show up
    in any paste of the deployed script."""
    assert "hermes_agent_vikunja_bridge_token" not in TEMPLATE, (
        "the token must arrive via the EnvironmentFile, never be rendered here")
    assert 'os.environ.get("VIKUNJA_API_TOKEN"' in TEMPLATE
    assert "hermes_agent_vikunja_bridge_token" in ENV_TEMPLATE
    assert re.search(r'dest:.*vikunja_bridge_env_file.*\n(.*\n)*?\s+mode: "0600"', TASKS), (
        "the env file must be deployed 0600")
    assert "no_log: true" in TASKS


def test_the_card_body_forbids_a_second_writer_to_the_operators_board():
    """The bridge is the single writer to Vikunja. If a worker also wrote
    there, the board could show two different verdicts for one card and the
    operator would have no way to tell which one the system believed."""
    body = BRIDGE.card_body({"title": "T", "description": "D"})
    assert "Do NOT write to Vikunja yourself" in body
    assert "kanban_complete" in body and "kanban_block" in body, (
        "the worker must be told how to end the card, or it ends with text alone")
    assert "EVIDENCE CONTRACT" in body


def test_the_ledger_degrades_to_empty_rather_than_to_garbage():
    """Every unusable state file must read as an empty ledger. That is safe
    because re-intake is idempotent through the card's idempotency key, whereas
    a half-parsed ledger would mean acting on a card id that is not real."""
    state = Path(BRIDGE.STATE_PATH)
    state.parent.mkdir(parents=True, exist_ok=True)
    for bad in ("", "not json", "[]", '{"schema": 999, "tracked": {"1": "c"}}',
                '{"schema": 1, "tracked": "nope"}'):
        state.write_text(bad)
        assert BRIDGE.load_ledger() == {}, bad

    BRIDGE.save_ledger({"1": "card-1"})
    assert BRIDGE.load_ledger() == {"1": "card-1"}, "a well-formed ledger must round-trip"


def test_intake_is_keyed_on_the_vikunja_task_so_a_crash_cannot_double_dispatch():
    """The window between `kanban create` and the ledger write is the only place
    a crash could duplicate work. The idempotency key closes it: a re-intake
    returns the existing card instead of starting the job twice."""
    create = TEMPLATE.split("def create_card")[1].split("\ndef ")[0]
    assert '"--idempotency-key", f"vikunja-{task[\'id\']}"' in create, create


def test_the_bridge_is_off_by_default_and_asserts_its_own_credential():
    defaults = role_defaults(DEFAULTS_PATH)
    assert defaults["hermes_agent_vikunja_bridge_enabled"] is False, (
        "a bridge that writes to the operator's board must be opted into")
    # It does not need — and must not silently depend on — the read-only MCP
    # route: every operation it performs is a write.
    assert defaults["hermes_agent_vikunja_mcp_enabled"] is False

    asserts = role_tasks_text(ROLE, "assert.yml")
    assert "hermes_agent_vikunja_bridge_token | length > 0" in asserts, (
        "enabling the bridge with no token must fail the converge, not ship a no-op")
    assert "hermes_agent_vikunja_bridge_card_assignee" in asserts


def test_the_unit_restarts_forever_and_reads_its_env_file():
    assert "Restart=always" in SERVICE
    assert "EnvironmentFile={{ hermes_agent_vikunja_bridge_env_file }}" in SERVICE
    # Wants, not Requires: a gateway restart must not take the bridge down.
    assert "Wants=network-online.target" in SERVICE and "Requires=" not in SERVICE


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
