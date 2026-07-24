"""Contract tests for the operator Kanban board.

The board is a face over `hermes kanban`, so the load-bearing logic is
`build_argv`: it decides which CLI verbs may run and rejects everything else.
These tests exercise it directly — no HTTP, no live guest.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOARD_PY = REPO_ROOT / "roles/hermes_agent/files/kanban-board.py"
TASKS = (REPO_ROOT / "roles/hermes_agent/tasks/main.yml").read_text()
DEFAULTS = (REPO_ROOT / "roles/hermes_agent/defaults/main.yml").read_text()
UNIT = (REPO_ROOT / "roles/hermes_agent/templates/hermes-kanban-board.service.j2").read_text()


def _load_board():
    spec = importlib.util.spec_from_file_location("kanban_board", BOARD_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


board = _load_board()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"action": "promote", "id": "t_aa79b03e"}, ["promote", "t_aa79b03e"]),
        ({"action": "archive", "id": "t_aa79b03e"}, ["archive", "t_aa79b03e"]),
        (
            {"action": "complete", "id": "t_aa79b03e", "text": "shipped"},
            ["complete", "t_aa79b03e", "--result", "shipped"],
        ),
        (
            {"action": "unblock", "id": "t_aa79b03e", "text": "answered"},
            ["unblock", "t_aa79b03e", "--reason", "answered"],
        ),
        (
            {"action": "comment", "id": "t_aa79b03e", "text": "looked at this"},
            ["comment", "t_aa79b03e", "looked at this"],
        ),
        ({"action": "create", "title": "new work"}, ["create", "new work"]),
        (
            {"action": "create", "title": "new work", "text": "details"},
            ["create", "new work", "--body", "details"],
        ),
    ],
)
def test_allowed_actions_build_expected_argv(payload, expected):
    argv, error = board.build_argv(payload)
    assert error is None
    assert argv == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "daemon", "id": "t_aa79b03e"},  # real verb, not allow-listed
        {"action": "gc", "id": "t_aa79b03e"},
        {"action": None, "id": "t_aa79b03e"},
        {"action": "promote", "id": "--help"},  # option smuggled as an id
        {"action": "promote", "id": "t_aa; rm -rf /"},
        {"action": "promote", "id": ""},
        {"action": "comment", "id": "t_aa79b03e"},  # comment needs a body
        {"action": "create", "title": "   "},
        {"action": "complete", "id": "t_aa79b03e", "text": "x" * (board.MAX_TEXT + 1)},
    ],
)
def test_rejected_requests_return_an_error_and_no_argv(payload):
    argv, error = board.build_argv(payload)
    assert argv is None
    assert error


def test_every_allowed_action_is_a_kanban_verb_not_a_shell_string():
    # argv elements are passed to subprocess as a list; a verb that arrived as
    # one space-joined string would mean the board shelled out.
    for action in board.ACTIONS:
        argv, error = board.build_argv({"action": action, "id": "t_aa79b03e", "text": "note"})
        assert error is None, action
        assert argv[0] == action
        assert " " not in argv[0]


def test_board_hides_archived_and_covers_every_live_status():
    # Columns mirror `hermes kanban list --status` choices minus `archived`.
    assert "archived" not in board.COLUMNS
    assert set(board.COLUMNS) == {
        "triage",
        "todo",
        "ready",
        "running",
        "review",
        "blocked",
        "scheduled",
        "done",
    }


def test_role_deploys_board_behind_its_enable_flag():
    assert "src: kanban-board.py" in TASKS
    assert "hermes-kanban-board.service.j2" in TASKS
    assert TASKS.count("when: hermes_agent_kanban_board_enabled | bool") == 3
    assert "hermes_agent_kanban_board_enabled: true" in DEFAULTS


def test_port_comes_from_tofu_constants_never_a_literal():
    assert "service_ports']['hermes_kanban']" in DEFAULTS
    assert "KANBAN_BOARD_PORT={{ hermes_agent_kanban_board_port }}" in UNIT
