"""Test the agent-key scheduler-priority default without LiteLLM runtime imports."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CALLBACK = REPO_ROOT / "roles/llm_router/files/callbacks/agent_key_priority.py"


@pytest.fixture
def priority_handler(monkeypatch):
    litellm = types.ModuleType("litellm")
    litellm.__path__ = []
    integrations = types.ModuleType("litellm.integrations")
    integrations.__path__ = []
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.integrations", integrations)
    monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger", custom_logger)
    namespace = {"__name__": "agent_key_priority_test"}
    exec(compile(CALLBACK.read_text(), str(CALLBACK), "exec"), namespace)  # noqa: S102 - callback source under test
    return namespace["proxy_handler_instance"]


def _apply(handler, key_metadata, data):
    api_key = types.SimpleNamespace(metadata=key_metadata)
    return asyncio.run(handler.async_pre_call_hook(api_key, cache=None, data=data, call_type="acompletion"))


def test_agent_key_defaults_request_priority_to_ten(priority_handler):
    request = {"model": "medium", "messages": []}
    result = _apply(priority_handler, {"router_key_class": "agent", "router_request_priority": 10}, request)
    assert result["priority"] == 10


def test_agent_key_overrides_a_client_request_priority(priority_handler):
    request = {"model": "medium", "messages": [], "priority": 0}
    result = _apply(priority_handler, {"router_key_class": "agent", "router_request_priority": 10}, request)
    assert result["priority"] == 10


def test_interactive_key_receives_no_agent_priority(priority_handler):
    request = {"model": "medium", "messages": []}
    result = _apply(priority_handler, {"trace_defaults": {"client": "claude-code"}}, request)
    assert "priority" not in result
