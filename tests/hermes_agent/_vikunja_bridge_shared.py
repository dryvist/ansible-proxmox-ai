"""Shared fixtures for the Vikunja <-> Hermes Kanban bridge contract checks.

Split out so test_vikunja_bridge.py (the original card-lifecycle/board-route
contracts) and test_vikunja_bridge_lanes.py (needs_triage/busy lane sweeps)
both stay under the repo's per-file token budget — see .token-limits.yaml.
A version bump to either file's test list never touches this one.
"""
import re
import sqlite3
import tempfile
import types
from pathlib import Path

from _role_files import role_defaults, role_tasks_text

REPO_ROOT = Path(__file__).resolve().parents[2]
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
    "BUCKET_NEEDS_TRIAGE": "Needs Triage",
    "BUCKET_BUSY": "Busy",
    "BLOCKED_TRIAGE_DAYS": 7,
    "BUSY_REQUEUE_SECONDS": 300,
    "PROBE_STATE_PATH": str(Path(STATE_DIR) / "brain-watchdog" / "probe_state"),
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


class _patch:
    """Swap module-level attributes on BRIDGE for the block, then restore.

    The bridge's functions call each other by module-global lookup, so
    patching the module attribute intercepts a call without ever reaching
    Vikunja or kanban.db — the same posture as the rest of this suite, which
    never lets a test touch the network.
    """

    def __init__(self, **attrs):
        self.attrs = attrs
        self.saved = {}

    def __enter__(self):
        for name, value in self.attrs.items():
            self.saved[name] = getattr(BRIDGE, name)
            setattr(BRIDGE, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(BRIDGE, name, value)


def board_db():
    """An in-memory stand-in with kanban.db's real column shape."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE tasks (id TEXT, status TEXT, consecutive_failures INT,"
        " max_retries INT);"
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT,"
        " outcome TEXT, summary TEXT, error TEXT, ended_at REAL);")
    return conn
