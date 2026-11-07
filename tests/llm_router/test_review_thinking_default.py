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


def _load() -> dict:
    stub = types.ModuleType("litellm.integrations.custom_logger")
    setattr(stub, "CustomLogger", type("CustomLogger", (), {}))
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    out: dict = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), out)  # noqa: S102 - our own file
    return out


_NAMESPACE = _load()
KEYS = sorted(_NAMESPACE["_CI_KEY_ALIASES"])
# The review role names are registry values, so they are read from the callback's own set, never re-typed here.
LOCAL, OSS, PRIVATE = (
    next(m for m in sorted(_NAMESPACE["_THINKING_OFF_MODELS"]) if m.endswith(scope)) for scope in ("local", "oss", "private")
)


@pytest.fixture(scope="module")
def handler():
    return _NAMESPACE["proxy_handler_instance"]


def call(handler, alias, data, call_type="acompletion"):
    if alias in _NAMESPACE["_CI_KEY_ALIASES"]:
        data["proxy_server_request"] = {"body": dict(data)}
    key = types.SimpleNamespace(key_alias=alias)
    out = asyncio.run(handler.async_pre_call_hook(key, None, data, call_type))
    if isinstance(out, dict):
        out.pop("proxy_server_request", None)
    return out


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("model", [LOCAL, PRIVATE, OSS])
@pytest.mark.parametrize("call_type", ["completion", "acompletion"])
def test_chat_call_to_a_review_role_gets_thinking_off_at_top_level(handler, alias, model, call_type):
    # Top level, never extra_body: a request extra_body replaces the deployment's whole extra_body on a fallback hop.
    out = call(handler, alias, {"model": model, "messages": []}, call_type)
    assert out == {"model": model, "messages": [], "chat_template_kwargs": THINKING_OFF}


@pytest.mark.parametrize("alias", KEYS)
def test_chat_call_to_the_medium_capability_gets_thinking_off_at_top_level(handler, alias):
    out = call(handler, alias, {"model": "medium", "messages": []})
    assert out == {"model": "medium", "messages": [], "chat_template_kwargs": THINKING_OFF}


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("enabled", [True, False])
def test_client_choice_in_extra_body_is_kept_and_not_duplicated(handler, alias, enabled):
    extra = {"chat_template_kwargs": {"enable_thinking": enabled}}
    out = call(handler, alias, {"model": LOCAL, "extra_body": copy.deepcopy(extra)})
    assert out["extra_body"] == extra
    assert "chat_template_kwargs" not in out


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("enabled", [True, False])
def test_client_choice_at_top_level_is_pinned_off(handler, alias, enabled):
    template = {"enable_thinking": enabled}
    out = call(handler, alias, {"model": OSS, "chat_template_kwargs": dict(template)})
    assert out["chat_template_kwargs"] == THINKING_OFF
    assert "extra_body" not in out


@pytest.mark.parametrize("alias", KEYS)
def test_other_template_flags_at_top_level_are_replaced_by_the_default(handler, alias):
    out = call(handler, alias, {"model": OSS, "chat_template_kwargs": {"preserve_thinking": True}})
    assert out["chat_template_kwargs"] == THINKING_OFF


@pytest.mark.parametrize("alias", KEYS)
def test_other_template_flags_in_extra_body_are_replaced_without_mutating_the_request(handler, alias):
    extra = {"provider": {"order": ["a"]}, "chat_template_kwargs": {"preserve_thinking": True}}
    original = copy.deepcopy(extra)
    out = call(handler, alias, {"model": OSS, "extra_body": extra})
    assert "extra_body" not in out
    assert out["chat_template_kwargs"] == THINKING_OFF
    assert extra == original


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("extra", [{"provider": {"order": ["a"]}}, {}, None, "text", ["x"]])
def test_extra_body_without_template_is_dropped_and_default_goes_top_level(handler, alias, extra):
    out = call(handler, alias, {"model": OSS, "extra_body": copy.deepcopy(extra)})
    assert "extra_body" not in out
    assert out["chat_template_kwargs"] == THINKING_OFF


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("template", ["x", ["x"], 1])
def test_malformed_top_level_template_is_replaced_by_the_default(handler, alias, template):
    out = call(handler, alias, {"model": OSS, "chat_template_kwargs": template})
    assert out["chat_template_kwargs"] == THINKING_OFF


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("template", ["x", ["x"], None])
def test_malformed_extra_body_template_is_replaced_by_the_default(handler, alias, template):
    extra = {"chat_template_kwargs": template}
    out = call(handler, alias, {"model": OSS, "extra_body": copy.deepcopy(extra)})
    assert "extra_body" not in out
    assert out["chat_template_kwargs"] == THINKING_OFF


@pytest.mark.parametrize("model", ["m", "review", "cloud-rung-under-test"])
@pytest.mark.parametrize("alias", KEYS)
def test_other_models_are_untouched(handler, alias, model):
    assert call(handler, alias, {"model": model, "messages": []}) == {"model": model, "messages": []}


@pytest.mark.parametrize("alias", ["open-webui", "github-actions-ci-fix", None])
@pytest.mark.parametrize("model", [LOCAL, "medium"])
def test_other_keys_are_untouched(handler, alias, model):
    data = {"model": model, "messages": []}
    assert call(handler, alias, dict(data)) == data


@pytest.mark.parametrize("call_type", ["responses", "aresponses", "anthropic_messages", "aanthropic_messages"])
def test_non_chat_routes_are_untouched(handler, call_type):
    out = call(handler, "github-actions", {"model": PRIVATE}, call_type)
    assert out == {"model": PRIVATE}


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("call_type", ["aembedding", "arerank", "atext_completion"])
def test_ci_keys_reject_unsupported_routes(handler, alias, call_type):
    out = call(handler, alias, {"model": OSS, "input": "x"}, call_type)
    assert isinstance(out, str)


@pytest.mark.parametrize("alias", KEYS)
@pytest.mark.parametrize("model", [[OSS], {"a": 1}])
def test_ci_key_rejects_non_string_model(handler, alias, model):
    data = {"model": model, "messages": []}
    assert isinstance(call(handler, alias, dict(data)), str)


@pytest.mark.parametrize("alias", KEYS)
def test_ci_key_without_model_does_not_raise(handler, alias):
    data = {"model": None, "messages": []}
    assert call(handler, alias, dict(data)) == data
