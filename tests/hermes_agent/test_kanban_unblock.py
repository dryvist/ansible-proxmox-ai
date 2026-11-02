"""Converge-time Kanban unblock: native CLI, guarded to cards blocked right now."""

from __future__ import annotations

import json

import yaml
from jinja2 import Environment

from conftest import ROLE_ROOT
from _role_files import role_defaults

TASKS = yaml.safe_load((ROLE_ROOT / "tasks" / "kanban_unblock.yml").read_text())


def _env() -> Environment:
    env = Environment()
    env.filters["from_json"] = json.loads
    env.filters["intersect"] = lambda a, b: [x for x in a if x in b]
    return env


def _select(requested: list[str], board: list[dict]) -> list[str]:
    expr = TASKS[1]["ansible.builtin.set_fact"]["hermes_agent_kanban_unblock_ids"]
    rendered = _env().from_string(expr).render(
        hermes_agent_kanban_unblock=requested,
        hermes_agent_kanban_blocked_raw={"stdout": json.dumps(board)},
    )
    return yaml.safe_load(rendered.strip())


def test_default_unblocks_nothing() -> None:
    assert role_defaults(ROLE_ROOT)["hermes_agent_kanban_unblock"] == []


def test_only_requested_cards_that_are_blocked_are_selected() -> None:
    board = [
        {"id": "t_aaaaaaaa", "status": "blocked"},
        {"id": "t_bbbbbbbb", "status": "blocked"},
        {"id": "t_cccccccc", "status": "ready"},
    ]
    assert _select(["t_aaaaaaaa", "t_cccccccc", "t_dddddddd"], board) == ["t_aaaaaaaa"]
    assert _select(["t_aaaaaaaa"], []) == []


def test_read_is_side_effect_free_and_unblock_runs_as_hermes_only_when_needed() -> None:
    read, _, unblock = TASKS
    assert read["ansible.builtin.command"]["argv"][1:] == ["kanban", "list", "--status", "blocked", "--json"]
    assert read["changed_when"] is False
    assert "'kanban', 'unblock'" in unblock["ansible.builtin.command"]["argv"]
    assert unblock["become_user"] == "{{ hermes_agent_user }}"
    assert unblock["when"] == "hermes_agent_kanban_unblock_ids | length > 0"
