"""Chat calls from the CI keys to a review role default to thinking off, and nothing else changes.
Loaded from source without importing litellm (CustomLogger is stubbed), like test_ci_effort_pin.py."""

from __future__ import annotations

import asyncio
import copy
import sys
import types
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "roles/llm_router/files/callbacks/ci_effort_pin.py"
THINKING_OFF = {"enable_thinking": False}
KEYS = ["github-actions", "github-actions-oss"]


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
        data["proxy_server_request"] = {"body": copy.deepcopy(data)}
    key = types.SimpleNamespace(key_alias=alias)
    out = asyncio.run(handler.async_pre_call_hook(key, None, data, call_type))
    if isinstance(out, dict):
        out.pop("proxy_server_request", None)
    return out


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("model", ["review-local", "review-private", "review-oss"])
@pytest.mark.parametrize("call_type", ["completion", "acompletion"])
def test_chat_call_to_a_review_role_gets_thinking_off_at_top_level(handler, alias, model, call_type):
    # Top level, never extra_body: a request extra_body replaces the deployment's whole extra_body on a fallback hop.
    out = call(handler, alias, {"model": model, "messages": []}, call_type)
    assert out == {"model": model, "messages": [], "chat_template_kwargs": THINKING_OFF}


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("enabled", [True, False])
def test_client_choice_in_extra_body_is_kept_and_not_duplicated(handler, alias, enabled):
    extra = {"chat_template_kwargs": {"enable_thinking": enabled}}
    out = call(handler, alias, {"model": "review-local", "extra_body": copy.deepcopy(extra)})
    assert out["extra_body"] == extra
    assert "chat_template_kwargs" not in out


@pytest.mark.parametrize("enabled", [True, False])
def test_oss_client_choice_at_top_level_is_kept(handler, enabled):
    template = {"enable_thinking": enabled}
    out = call(handler, "github-actions-oss", {"model": "review-oss", "chat_template_kwargs": dict(template)})
    assert out["chat_template_kwargs"] == template
    assert "extra_body" not in out


def test_oss_other_template_flags_at_top_level_get_the_default_added(handler):
    out = call(handler, "github-actions-oss", {"model": "review-oss", "chat_template_kwargs": {"preserve_thinking": True}})
    assert out["chat_template_kwargs"] == {"preserve_thinking": True, **THINKING_OFF}


def test_oss_other_template_flags_in_extra_body_get_the_default_added_without_mutating_the_request(handler):
    extra = {"provider": {"order": ["a"]}, "chat_template_kwargs": {"preserve_thinking": True}}
    original = copy.deepcopy(extra)
    out = call(handler, "github-actions-oss", {"model": "review-oss", "extra_body": extra})
    assert out["extra_body"] == {
        "provider": {"order": ["a"]},
        "chat_template_kwargs": {"preserve_thinking": True, **THINKING_OFF},
    }
    assert extra == original
    assert "chat_template_kwargs" not in out


@pytest.mark.parametrize("extra", [{"provider": {"order": ["a"]}}, {}, None, "text", ["x"]])
def test_oss_extra_body_without_template_is_untouched_and_default_goes_top_level(handler, extra):
    out = call(handler, "github-actions-oss", {"model": "review-oss", "extra_body": copy.deepcopy(extra)})
    assert out["extra_body"] == extra
    assert out["chat_template_kwargs"] == THINKING_OFF


@pytest.mark.parametrize("template", ["x", ["x"], 1])
def test_oss_malformed_top_level_template_is_left_alone(handler, template):
    out = call(handler, "github-actions-oss", {"model": "review-oss", "chat_template_kwargs": template})
    assert out["chat_template_kwargs"] == template


@pytest.mark.parametrize("template", ["x", ["x"], None])
def test_oss_malformed_extra_body_template_is_left_alone(handler, template):
    extra = {"chat_template_kwargs": template}
    out = call(handler, "github-actions-oss", {"model": "review-oss", "extra_body": copy.deepcopy(extra)})
    assert out["extra_body"] == extra
    assert "chat_template_kwargs" not in out


@pytest.mark.parametrize("model", ["m", "review", "codex-ci"])
@pytest.mark.parametrize("alias", KEYS)
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


@pytest.mark.parametrize("model", [["review-oss"], {"a": 1}, None])
def test_oss_key_non_string_model_does_not_raise(handler, model):
    data = {"model": model, "messages": []}
    assert call(handler, "github-actions-oss", dict(data)) == data
