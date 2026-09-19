"""merge_system_messages folds every system/developer message into one leading
system message and leaves already-conforming lists untouched. Loaded from source
without importing litellm (CustomLogger is stubbed), like the lock tests."""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "roles/llm_router/files/callbacks/system_merge.py"


@pytest.fixture(scope="module")
def merge():
    stub = types.ModuleType("litellm.integrations.custom_logger")
    stub.CustomLogger = type("CustomLogger", (), {})
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    ns: dict = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns)  # noqa: S102 - our own file
    return ns["merge_system_messages"]


def test_source_parses():
    ast.parse(SOURCE.read_text())


def test_single_leading_system_is_untouched(merge):
    msgs = [{"role": "system", "content": "a"}, {"role": "user", "content": "hi"}]
    assert merge(msgs) is msgs


def test_no_system_is_untouched(merge):
    msgs = [{"role": "user", "content": "hi"}]
    assert merge(msgs) is msgs


def test_instructions_plus_developer_become_one_system(merge):
    msgs = [
        {"role": "system", "content": "instructions"},
        {"role": "developer", "content": "developer"},
        {"role": "user", "content": "go"},
    ]
    assert merge(msgs) == [
        {"role": "system", "content": "instructions\n\ndeveloper"},
        {"role": "user", "content": "go"},
    ]


def test_mid_thread_system_moves_to_front_keeping_turn_order(merge):
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "system", "content": "late"},
        {"role": "user", "content": "go"},
    ]
    assert merge(msgs) == [
        {"role": "system", "content": "late"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "go"},
    ]


def test_content_parts_are_flattened(merge):
    msgs = [
        {"role": "developer", "content": [{"type": "text", "text": "p1"}, {"type": "text", "text": "p2"}]},
        {"role": "system", "content": "s"},
        {"role": "user", "content": "go"},
    ]
    assert merge(msgs)[0] == {"role": "system", "content": "p1\np2\n\ns"}
