"""Test the lane response header without importing the LiteLLM runtime."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CALLBACK = REPO_ROOT / "roles/llm_router/files/callbacks/lane_header.py"
TARGET_KEYS = REPO_ROOT / "roles/llm_router/defaults/main/56a-target-keys.yml"
LANE_UNDER_TEST = "lane-under-test"


@pytest.fixture
def lane_handler(monkeypatch):
    monkeypatch.setenv("LLM_ROUTER_LANE_NAMES", f"{LANE_UNDER_TEST}, another-lane")
    litellm = types.ModuleType("litellm")
    litellm.__path__ = []
    integrations = types.ModuleType("litellm.integrations")
    integrations.__path__ = []
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.integrations", integrations)
    monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger", custom_logger)
    namespace = {"__name__": "lane_header_test"}
    exec(compile(CALLBACK.read_text(), str(CALLBACK), "exec"), namespace)  # noqa: S102 - callback source under test
    return namespace["proxy_handler_instance"]


def _headers(handler, data):
    return asyncio.run(
        handler.async_post_call_response_headers_hook(
            data=data,
            user_api_key_dict=None,
            response=None,
        )
    )


def test_header_is_set_for_a_lane_name(lane_handler):
    assert _headers(lane_handler, {"model": LANE_UNDER_TEST}) == {"X-LiteLLM-Lane": LANE_UNDER_TEST}


def test_header_is_absent_for_a_non_lane_name(lane_handler):
    assert _headers(lane_handler, {"model": "not-a-lane"}) is None


def test_missing_model_does_not_raise(lane_handler):
    assert _headers(lane_handler, {}) is None
    assert _headers(lane_handler, None) is None


def test_probe_key_excludes_local_and_uses_lane_projection():
    keys = yaml.safe_load(TARGET_KEYS.read_text())["_llm_router_target_virtual_keys"]
    key = next(item for item in keys if item["alias"] == "lane-walk-probe")
    assert "!local" in key["tags"]
    assert "&local" not in key["tags"]
    assert key["daily_budget"] == 1.0
    assert key["models"] == "{{ llm_router_lane_names }}"
