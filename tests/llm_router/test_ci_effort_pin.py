"""The CI key is held to its served routes at the registry effort, and no other key is touched.
Loaded from source without importing litellm (CustomLogger is stubbed), like
test_system_merge.py."""

from __future__ import annotations

import ast
import asyncio
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "roles/llm_router/files/callbacks/ci_effort_pin.py"
CI_ALIAS = "github-actions"


@pytest.fixture(scope="module")
def ns():
    stub = types.ModuleType("litellm.integrations.custom_logger")
    stub.CustomLogger = type("CustomLogger", (), {})
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    out: dict = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), out)  # noqa: S102 - our own file
    return out


def _call(ns, alias, data, call_type="acompletion"):
    key = types.SimpleNamespace(key_alias=alias)
    handler = ns["proxy_handler_instance"]
    return asyncio.run(handler.async_pre_call_hook(key, None, data, call_type))


def test_source_parses():
    ast.parse(SOURCE.read_text())


def test_ci_key_loses_every_effort_carrier(ns):
    data = {
        "model": "review-private",
        "reasoning_effort": "low",
        "thinking": {"type": "enabled"},
        "reasoning": {"effort": "low"},
        "output_config": {"effort": "low"},
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = _call(ns, CI_ALIAS, data)
    assert out["model"] == "review-private"
    assert out["messages"] == [{"role": "user", "content": "hi"}]
    for field in ("reasoning_effort", "thinking", "reasoning", "output_config"):
        assert field not in out


def test_dict_fallback_entries_cannot_carry_effort(ns):
    # A client fallback entry given as a dict is merged into the request on the
    # fallback hop, after this hook, so the lists must go.
    data = {
        "model": "review-private",
        "context_window_fallbacks": [{"model": "codex-ci", "reasoning_effort": "low"}],
        "fallbacks": [{"model": "codex-ci", "reasoning_effort": "low"}],
        "content_policy_fallbacks": [{"model": "codex-ci", "reasoning_effort": "low"}],
    }
    out = _call(ns, CI_ALIAS, data)
    for field in ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks"):
        assert field not in out


def test_router_settings_override_cannot_smuggle_fallback_entries(ns):
    # route_llm_request copies fallback lists out of this override when the
    # request has none, so a dict entry inside it would carry request fields too.
    data = {
        "model": "review-private",
        "router_settings_override": {
            "context_window_fallbacks": [{"model": "codex-ci", "reasoning_effort": "low"}],
            "fallbacks": [{"model": "codex-ci", "reasoning_effort": "low"}],
        },
        "_router_weights": {"codex-ci": 1},
        "custom_llm_provider": "chatgpt",
    }
    out = _call(ns, CI_ALIAS, data)
    assert out == {"model": "review-private"}


def test_compact_route_cannot_pick_a_deployment_by_response_id(ns):
    # The router decodes an unsigned deployment id out of response_id on the
    # compact route and routes there without a key-scope check.
    data = {"model": "review-private", "input": "hi", "response_id": "resp_forged-deployment-id"}
    assert _call(ns, CI_ALIAS, data, call_type="acompact_responses") == {"model": "review-private", "input": "hi"}


def test_additional_drop_params_cannot_remove_the_deployment_effort(ns):
    out = _call(ns, CI_ALIAS, {"model": "review-private", "additional_drop_params": ["reasoning_effort"]})
    assert "additional_drop_params" not in out


def test_extra_body_keeps_only_the_thinking_toggle(ns):
    data = {
        "model": "review-private",
        "extra_body": {"model": "other", "reasoning_effort": "low", "chat_template_kwargs": {"enable_thinking": False}},
    }
    assert _call(ns, CI_ALIAS, data)["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_foreign_or_non_dict_extra_body_is_dropped(ns):
    assert "extra_body" not in _call(ns, CI_ALIAS, {"model": "m", "extra_body": {"model": "other"}})
    assert "extra_body" not in _call(ns, CI_ALIAS, {"model": "m", "extra_body": '{"model": "other"}'})


@pytest.mark.parametrize("call_type", ["_aresponses_websocket", "aembedding", "arerank", "atext_completion", "aget_responses"])
def test_ci_key_is_rejected_off_the_served_routes(ns, call_type):
    out = _call(ns, CI_ALIAS, {"model": "codex-ci", "extra_body": {"model": "other"}}, call_type=call_type)
    assert isinstance(out, str)


@pytest.mark.parametrize(
    "call_type",
    [
        "completion",
        "acompletion",
        "responses",
        "aresponses",
        "compact_responses",
        "acompact_responses",
        "anthropic_messages",
        "aanthropic_messages",
    ],
)
def test_ci_key_served_call_types_are_pinned(ns, call_type):
    # Codex jobs call Responses and Claude jobs call Anthropic messages with the
    # same key; both reach the same deployments, so the same pins apply.
    data = {
        "model": "review-private",
        "reasoning": {"effort": "low"},
        "thinking": {"type": "enabled", "budget_tokens": 1},
        "output_config": {"effort": "low"},
        "extra_body": {"model": "gpt-5.6-sol", "chat_template_kwargs": {"enable_thinking": False}},
        "input": "hi",
    }
    out = _call(ns, CI_ALIAS, data, call_type=call_type)
    assert out == {
        "model": "review-private",
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        "input": "hi",
    }


@pytest.mark.parametrize("alias", ["github-actions-oss", "open-webui", None])
def test_other_keys_keep_every_request_control(ns, alias):
    data = {
        "model": "m",
        "reasoning_effort": "low",
        "fallbacks": [{"model": "x"}],
        "additional_drop_params": ["a"],
        "extra_body": {"reasoning_effort": "low", "model": "other"},
    }
    snapshot = {k: (dict(v) if isinstance(v, dict) else v) for k, v in data.items()}
    assert _call(ns, alias, data) == snapshot
    assert _call(ns, alias, dict(data), call_type="aresponses") == snapshot


def test_request_without_effort_is_untouched(ns):
    data = {"model": "review-private", "messages": []}
    assert _call(ns, CI_ALIAS, dict(data)) == data
