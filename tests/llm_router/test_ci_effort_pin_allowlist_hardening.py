"""Malformed client values the CI key guard narrows instead of passing through."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "roles/llm_router/files/callbacks/ci_effort_pin.py"
CALL_TYPES = ["acompletion", "aresponses", "aanthropic_messages"]


@pytest.fixture(scope="module")
def handler():
    stub = types.ModuleType("litellm.integrations.custom_logger")
    setattr(stub, "CustomLogger", type("CustomLogger", (), {}))
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    spec = importlib.util.spec_from_file_location("ci_effort_pin_under_test", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.proxy_handler_instance


def call(handler, data, call_type):
    key = types.SimpleNamespace(key_alias="github-actions")
    data.setdefault("proxy_server_request", {"body": {k: v for k, v in data.items() if k != "proxy_server_request"}})
    return asyncio.run(handler.async_pre_call_hook(key, None, data, call_type))


@pytest.mark.parametrize("call_type", CALL_TYPES)
def test_tool_with_non_string_type_is_dropped(handler, call_type):
    keep = {"name": "plain"}
    tools = [keep, {"type": ["web_search"]}, {"type": None}, {"type": 7}]
    assert call(handler, {"model": "m", "tools": tools}, call_type)["tools"] == [keep]


@pytest.mark.parametrize("call_type", CALL_TYPES)
def test_non_list_tools_are_removed(handler, call_type):
    assert "tools" not in call(handler, {"model": "m", "tools": {"type": "web_search"}}, call_type)


@pytest.mark.parametrize("value", ["high", 1, None, {"x": 1}])
def test_enable_thinking_must_be_a_boolean(handler, value):
    extra = {"chat_template_kwargs": {"enable_thinking": value}}
    assert "extra_body" not in call(handler, {"model": "m", "extra_body": extra}, "acompletion")


def test_non_dict_chat_template_kwargs_are_removed(handler):
    extra = {"chat_template_kwargs": "enable_thinking", "reasoning_effort": "high"}
    assert "extra_body" not in call(handler, {"model": "m", "extra_body": extra}, "acompletion")


def test_non_dict_extra_body_is_removed(handler):
    assert "extra_body" not in call(handler, {"model": "m", "extra_body": ["x"]}, "acompletion")


def test_stream_options_replaced_by_the_proxy_still_narrow_to_include_usage(handler):
    sent = {"include_usage": True}
    data = {"model": "m", "stream_options": {"include_usage": True, "reasoning_effort": "high"}}
    data["proxy_server_request"] = {"body": {"model": "m", "stream_options": sent}}
    assert call(handler, data, "acompletion")["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize("call_type", CALL_TYPES)
def test_header_derived_retry_and_timeout_values_are_removed(handler, call_type):
    sent = {"model": "m"}
    carriers = {
        "num_retries": 50,
        "timeout": 1.0,
        "stream_timeout": 1.0,
        "keepalive_seconds": 5.0,
        "client_side_timeout": True,
    }
    data = {**sent, **carriers, "proxy_server_request": {"body": dict(sent)}}
    assert not set(carriers) & set(call(handler, data, call_type))


@pytest.mark.parametrize("call_type", CALL_TYPES)
@pytest.mark.parametrize("model", [["a", "b"], ("a",), 7, {"a": 1}])
def test_model_must_be_a_single_string(handler, call_type, model):
    msg = "The CI key must select one model per request."
    assert call(handler, {"model": model}, call_type) == msg
    live = {"model": model, "proxy_server_request": {"body": {"model": "m"}}}
    assert call(handler, live, call_type) == msg
