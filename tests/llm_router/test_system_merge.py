"""merge_system_messages folds every system/developer message into one leading
system message and leaves already-conforming lists untouched. Loaded from source
without importing litellm (CustomLogger is stubbed), like the lock tests."""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "roles/llm_router/files/callbacks/system_merge.py"


@pytest.fixture(scope="module")
def ns():
    stub = types.ModuleType("litellm.integrations.custom_logger")
    stub.CustomLogger = type("CustomLogger", (), {})
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    ns: dict = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns)  # noqa: S102 - our own file
    return ns


@pytest.fixture(scope="module")
def merge(ns):
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


BRIDGED = [
    {"role": "system", "content": "instructions"},
    {"role": "developer", "content": "developer"},
    {"role": "user", "content": "go"},
]
MERGED = [{"role": "system", "content": "instructions\n\ndeveloper"}, {"role": "user", "content": "go"}]


def test_pre_call_hook_merges_chat_messages(ns):
    data = asyncio.run(ns["proxy_handler_instance"].async_pre_call_hook(None, None, {"messages": list(BRIDGED)}, "completion"))
    assert data["messages"] == MERGED


def test_pre_call_hook_leaves_a_responses_body_alone(ns):
    body = {"input": [{"role": "developer", "content": "d"}], "instructions": "i"}
    assert asyncio.run(ns["proxy_handler_instance"].async_pre_call_hook(None, None, dict(body), "responses")) == body


def test_deployment_hook_merges_the_bridged_messages(ns):
    # The Responses bridge adds its system message after async_pre_call_hook, so this hook is the one that sees it.
    kwargs = asyncio.run(
        ns["proxy_handler_instance"].async_pre_call_deployment_hook({"model": "m", "messages": list(BRIDGED)}, "acompletion")
    )
    assert kwargs == {"model": "m", "messages": MERGED}


def test_unhashable_role_is_left_in_place(ns):
    messages = [{"role": ["system"], "content": "x"}, {"role": "user", "content": "hi"}]
    kwargs = {"messages": list(messages)}
    out = asyncio.run(ns["proxy_handler_instance"].async_pre_call_deployment_hook(kwargs, "anthropic_messages"))
    assert out["messages"] == messages


def test_deployment_hook_without_messages_is_untouched(ns):
    kwargs = {"model": "m", "input": "x"}
    assert asyncio.run(ns["proxy_handler_instance"].async_pre_call_deployment_hook(dict(kwargs), "aembedding")) == kwargs


@pytest.mark.parametrize("call_type", ["anthropic_messages", "aanthropic_messages"])
@pytest.mark.parametrize(
    "system",
    ["top-level system", [{"type": "text", "text": "top-level system"}]],
)
def test_anthropic_messages_request_reaches_local_rung_with_one_system(ns, system, call_type):
    request = {
        "system": system,
        "messages": [
            {"role": "user", "content": "before"},
            {"role": "system", "content": [{"type": "text", "text": "message system"}]},
            {"role": "user", "content": "go"},
        ],
    }
    before_deployment = asyncio.run(
        ns["proxy_handler_instance"].async_pre_call_hook(None, None, request, call_type)
    )
    outgoing = asyncio.run(
        ns["proxy_handler_instance"].async_pre_call_deployment_hook(before_deployment, call_type)
    )

    # LiteLLM's Anthropic adapter turns the top-level system into the leading
    # chat message sent to an OpenAI-compatible local rung.
    rung_messages = []
    if outgoing.get("system"):
        rung_messages.append({"role": "system", "content": outgoing["system"]})
    rung_messages.extend(outgoing["messages"])
    system_positions = [i for i, message in enumerate(rung_messages) if message.get("role") == "system"]
    rung_status = 400 if len(system_positions) > 1 or (system_positions and system_positions[0] != 0) else 200
    assert rung_status == 200, "System message must be at the beginning."
    assert rung_messages == [
        {"role": "system", "content": "top-level system\n\nmessage system"},
        {"role": "user", "content": "before"},
        {"role": "user", "content": "go"},
    ]
