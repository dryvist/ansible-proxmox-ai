"""Self-check for the Kanban digest's unspawnable-assignee diagnostic.

The gap it closes: "in_progress 0/1 (cap)" is true and useless when every ready
card names an assignee that is not a profile on disk. The dispatcher skips those
cards silently on every tick, so a dead board reads as a capped one — that is
how a board sat at 14 ready / 0 finished for 238 consecutive ticks (P1,
2026-08-11) with the alarm firing the whole time and saying nothing useful.

Split out of test_kanban_board_alarms.py: that module covers the three
board-health alarms, this one covers why a stall happened. Separate concerns,
and the per-file token budget wants files that do one job.
"""
import importlib.util
import re
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hermes_agent/templates/kanban-digest.py.j2"

TMP = Path(tempfile.mkdtemp(prefix="kanban-unspawnable-selfcheck-"))
FIXTURE_CONFIG = {
    "DB_PATH": str(TMP / "kanban.db"),
    "CONFIG_PATH": str(TMP / "config.yaml"),
    "PROFILES_DIR": str(TMP / "profiles"),
    "STATE_PATH": str(TMP / "kanban-digest.json"),
    "TITLE": "Kanban Board Digest",
    "INTERVAL_MIN": 15,
    "HEARTBEAT_HOURS": 6,
    "ISSUES_CHANNEL": "C_ISSUES",
    "NOISE_CHANNEL": "C_NOISE",
    "HERMES_BIN": str(TMP / "no-such-hermes-binary"),
    "ISSUES_MARKER": "[ISSUES]",
    "STALL_TICKS_THRESHOLD": 3,
    "TIMEOUT_RATE_THRESHOLD": 3,
    "BLOCKED_GROWTH_THRESHOLD": 3,
}


def load_digest_module():
    """Render the template's config lines to fixtures and load it as a module.

    Any new `NAME = ... {{ }}` constant the template gains must show up in
    FIXTURE_CONFIG too, or this assertion is what catches the drift.
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
    spec = importlib.util.spec_from_file_location("kanban_unspawnable", rendered_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DIGEST = load_digest_module()

NOW = 1785000000.0
NO_RUNS = []
NO_RUNNING = []


# --- the unspawnable-assignee diagnostic -------------------------------------
# "in_progress 0/1 (cap)" is true and useless when every ready card names a
# non-profile: the dispatcher skips them silently and the board looks capped
# rather than dead.
#
# Profiles are discovered by SCANNING PROFILES_DIR, not by importing the agent
# package. The import version shipped and then silently never fired: the digest
# runs as a --no-agent --script cron under system python, which cannot import
# hermes_cli, so the guard swallowed ImportError and returned '' forever. These
# tests build a real profiles directory so they exercise the same code path
# production does — a fake module would have passed while production stayed mute.
import contextlib
import os
import tempfile


@contextlib.contextmanager
def known_profiles(*names):
    root = tempfile.mkdtemp(prefix="kanban-profiles-")
    for n in names:
        if n == "default":
            continue  # implicit, never a directory
        os.makedirs(os.path.join(root, n))
        open(os.path.join(root, n, "config.yaml"), "w").close()
    prev = DIGEST.PROFILES_DIR
    setattr(DIGEST, "PROFILES_DIR", root)
    try:
        yield
    finally:
        setattr(DIGEST, "PROFILES_DIR", prev)


def test_stall_alarm_names_the_ready_assignees_that_are_not_real_profiles():
    rows = [{"assignee": "operator", "n": 9}, {"assignee": "hermes", "n": 5}]
    with known_profiles("default", "github-maint"):
        _, line = DIGEST.stall_alarm({"ready": 14, "running": 0}, NO_RUNS, NO_RUNNING,
                                     NOW, 2, 3, 1, rows)
    assert "14 ready card(s) name a non-profile assignee" in line
    assert "operator x9" in line and "hermes x5" in line
    assert "Known: default, github-maint" in line


def test_stall_alarm_adds_no_note_when_every_ready_assignee_is_real():
    """A valid-assignee stall is a different fault; a false accusation sends the
    operator hunting a profile problem that does not exist."""
    with known_profiles("default"):
        _, line = DIGEST.stall_alarm({"ready": 3, "running": 0}, NO_RUNS, NO_RUNNING,
                                     NOW, 2, 3, 1, [{"assignee": "default", "n": 3}])
    assert "non-profile" not in line


def test_unassigned_ready_cards_are_reported_under_a_readable_name():
    with known_profiles("default"):
        assert "(unassigned) x4" in DIGEST.unspawnable_note([{"assignee": "", "n": 4}])


def test_unspawnable_note_degrades_to_silence_when_profiles_are_unreadable():
    """Must still page. A crash report here would replace the stall alarm."""
    prev = DIGEST.PROFILES_DIR
    setattr(DIGEST, "PROFILES_DIR", "/nonexistent/profiles")
    try:
        assert DIGEST.unspawnable_note([{"assignee": "whoever", "n": 1}]) == ""
    finally:
        setattr(DIGEST, "PROFILES_DIR", prev)


def test_profile_discovery_does_not_import_the_agent_package():
    """The import version silently never fired in production: the digest runs
    under system python, which cannot import hermes_cli, so the guard swallowed
    the ImportError and returned '' forever. Scanning is what makes it work."""
    src = TEMPLATE_PATH.read_text()
    assert "list_profiles_on_disk" not in src
    assert "os.scandir(PROFILES_DIR)" in src
