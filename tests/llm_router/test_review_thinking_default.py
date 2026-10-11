"""Chat calls from the CI keys to a review role default to thinking off, and nothing else changes.
Loaded from source without importing litellm (CustomLogger is stubbed), like test_ci_effort_pin.py."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "roles/llm_router/files/callbacks/ci_effort_pin.py"
THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.fixture(scope="module")
def handler():
    stub = types.ModuleType("litellm.integrations.custom_logger")
    setattr(stub, "CustomLogger", type("CustomLogger", (), {}))
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    out: dict = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), out)  # noqa: S102 - our own file
    return out["proxy_handler_instance"]


def call(handler, alias, data, call_type="acompletion"):
    if alias == "github-actions":
        data["proxy_server_request"] = {"body": dict(data)}
    key = types.SimpleNamespace(key_alias=alias)
    out = asyncio.run(handler.async_pre_call_hook(key, None, data, call_type))
    if isinstance(out, dict):
        out.pop("proxy_server_request", None)
    return out


@pytest.mark.parametrize("alias", ["github-actions", "github-actions-oss"])
@pytest.mark.parametrize("model", ["review-local", "review-private", "review-oss"])
@pytest.mark.parametrize("call_type", ["completion", "acompletion"])
def test_chat_call_to_a_review_role_gets_thinking_off(handler, alias, model, call_type):
    out = call(handler, alias, {"model": model, "messages": []}, call_type)
    assert out == {"model": model, "messages": [], "extra_body": THINKING_OFF}


@pytest.mark.parametrize("alias", ["github-actions", "github-actions-oss"])
@pytest.mark.parametrize("enabled", [True, False])
def test_client_choice_of_thinking_is_kept(handler, alias, enabled):
    extra = {"chat_template_kwargs": {"enable_thinking": enabled}}
    out = call(handler, alias, {"model": "review-local", "extra_body": extra})
    assert out["extra_body"] == extra


@pytest.mark.parametrize("model", ["m", "review", "codex-ci"])
@pytest.mark.parametrize("alias", ["github-actions", "github-actions-oss"])
def test_other_models_are_untouched(handler, alias, model):
    assert call(handler, alias, {"model": model, "messages": []}) == {"model": model, "messages": []}


@pytest.mark.parametrize("alias", ["open-webui", "github-actions-ci-fix", None])
def test_other_keys_are_untouched(handler, alias):
    data = {"model": "review-local", "messages": []}
    assert call(handler, alias, dict(data)) == data


@pytest.mark.parametrize("call_type", ["responses", "aresponses", "anthropic_messages", "aanthropic_messages"])
def test_non_chat_routes_are_untouched(handler, call_type):
    out = call(handler, "github-actions", {"model": "review-private"}, call_type)
    assert out == {"model": "review-private"}


@pytest.mark.parametrize("call_type", ["aembedding", "arerank", "atext_completion"])
def test_oss_key_non_chat_calls_are_untouched(handler, call_type):
    data = {"model": "review-oss", "input": "x"}
    assert call(handler, "github-actions-oss", dict(data), call_type) == data


def test_oss_key_keeps_other_body_fields_and_adds_only_the_default(handler):
    data = {"model": "review-oss", "extra_body": {"provider": {"order": ["a"]}}}
    out = call(handler, "github-actions-oss", data)
    assert out["extra_body"] == {"provider": {"order": ["a"]}, **THINKING_OFF}


@pytest.mark.parametrize("extra", ["text", ["x"], {"chat_template_kwargs": "x"}, {"chat_template_kwargs": ["x"]}])
def test_oss_key_malformed_body_shapes_are_left_alone(handler, extra):
    out = call(handler, "github-actions-oss", {"model": "review-oss", "extra_body": extra})
    assert out["extra_body"] == extra


@pytest.mark.parametrize("model", [["review-oss"], {"a": 1}, None])
def test_oss_key_non_string_model_does_not_raise(handler, model):
    data = {"model": model, "messages": []}
    assert call(handler, "github-actions-oss", dict(data)) == data


def test_oss_key_template_flags_other_than_thinking_are_kept(handler):
    extra = {"chat_template_kwargs": {"preserve_thinking": True}}
    out = call(handler, "github-actions-oss", {"model": "review-oss", "extra_body": extra})
    assert out["extra_body"] == {"chat_template_kwargs": {"preserve_thinking": True, "enable_thinking": False}}
