"""Allow-list behavior against trimmed, captured LiteLLM proxy hook inputs."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "roles/llm_router/files/callbacks/ci_effort_pin.py"
FIXTURES = Path(__file__).with_name("fixtures") / "ci_effort_pin"
ALIAS = "github-actions"
KNOWN = (
    "reasoning_effort",
    "thinking",
    "reasoning",
    "output_config",
    "previous_response_id",
    "response_id",
    "api_key",
    "fallbacks",
    "context_window_fallbacks",
    "content_policy_fallbacks",
    "router_settings_override",
    "priority",
    "prompt_id",
    "allowed_model_region",
    "cache",
    "caching",
    "ttl",
    "num_retries",
    "timeout",
    "extra_headers",
    "headers",
    "provider_specific_header",
    "disable_fallbacks",
    "tags",
    "service_tier",
    "text",
)
REAL_FIXTURES = (
    "anthropic_messages__1.json",
    "aresponses__1.json",
    "acompact_responses__1.json",
    "acompletion__1.json",
    "acompletion__2.json",
    "acompletion__3.json",
)
CAPTURED_CLIENTS = (
    (
        "anthropic_messages",
        (
            "model",
            "messages",
            "system",
            "tools",
            "metadata",
            "max_tokens",
            "thinking",
            "context_management",
            "safeguards",
            "output_config",
            "stream",
        ),
        {"thinking", "output_config"},
    ),
    (
        "aresponses",
        (
            "model",
            "stream",
            "input",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "reasoning",
            "store",
            "include",
            "prompt_cache_key",
            "client_metadata",
        ),
        {"reasoning"},
    ),
)


@pytest.fixture(scope="module")
def handler():
    stub = types.ModuleType("litellm.integrations.custom_logger")
    stub.CustomLogger = type("CustomLogger", (), {})
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    ns = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns)  # noqa: S102 - loads this repository source
    return ns["proxy_handler_instance"]


def load_fixture(name):
    fixture = json.loads((FIXTURES / name).read_text())
    data = {}
    for key in fixture["top_level_keys"]:
        if key == "proxy_server_request":
            continue
        record = fixture["values"].get(key, {})
        if "value" in record:
            data[key] = copy.deepcopy(record["value"])
        else:
            data[key] = {"dict": {}, "list": [], "str": "fixture", "int": 1}.get(record.get("type"), "fixture")
    for key in ("metadata", "litellm_metadata"):
        metadata = data.get(key)
        if isinstance(metadata, dict):
            for tag_key in ("inherited_tags", "caller_tags"):
                if isinstance(metadata.get(tag_key), list):
                    metadata[tag_key] = tuple(metadata[tag_key])
    body = {}
    for key in fixture["proxy_server_request_body_keys"]:
        value = data[key]
        body[key] = value if fixture["identity"].get(key, False) else copy.deepcopy(value)
    data["proxy_server_request"] = {"body": body}
    return fixture, data, body


def call(handler, data, call_type):
    key = types.SimpleNamespace(key_alias=ALIAS)
    return asyncio.run(handler.async_pre_call_hook(key, None, data, call_type))


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_captured_client_fields_survive_with_only_route_controls_removed(handler, name):
    fixture, data, body = load_fixture(name)
    call_type = fixture["call_type"]
    for key in ("metadata", "litellm_metadata"):
        metadata = body.get(key)
        if isinstance(metadata, dict):
            for tag_key in ("inherited_tags", "caller_tags"):
                if tag_key in metadata:
                    assert isinstance(metadata[tag_key], tuple)
    out = call(handler, data, call_type)
    removed = {
        "anthropic_messages": {"thinking", "output_config"},
        "aresponses": {"reasoning"},
        "acompact_responses": {"reasoning"},
        "acompletion": {"reasoning_effort"},
    }[call_type]
    assert set(body) - set(out) == removed & set(body)
    if "extra_body" in body:
        assert set(out["extra_body"]) == {"chat_template_kwargs"}
        assert not {"service_tier", "processing_mode"} & set(out["extra_body"])
    if call_type == "acompletion" and "tags" in body["metadata"]:
        assert out["metadata"]["tags"] == []
        assert "tags" not in out["metadata"]["requester_metadata"]
    for key in ("metadata", "litellm_metadata"):
        metadata = out.get(key)
        if isinstance(metadata, dict) and "caller_tags" in metadata:
            assert metadata["caller_tags"] == ()


@pytest.mark.parametrize(("call_type", "keys", "removed"), CAPTURED_CLIENTS)
def test_captured_claude_and_codex_request_fields_remain_served(handler, call_type, keys, removed):
    body = {key: "client-value" for key in keys}
    body["tools"] = (
        [{"name": "claude-tool"}]
        if call_type == "anthropic_messages"
        else [{"type": kind, "name": kind} for kind in ("function", "namespace", "web_search")]
    )
    body["metadata" if call_type == "anthropic_messages" else "client_metadata"] = {"user_id": "client-user"}
    data = dict(body)
    data["proxy_server_request"] = {"body": dict(body)}
    out = call(handler, data, call_type)
    assert set(out) & set(keys) == set(keys) - removed
    expected_tools = ["function", "namespace"] if call_type == "aresponses" else [None]
    assert [tool.get("type") for tool in out["tools"]] == expected_tools


@pytest.mark.parametrize("call_type", ["acompletion", "aresponses", "aanthropic_messages"])
@pytest.mark.parametrize("field", KNOWN)
def test_known_effort_and_routing_carriers_drop_on_every_family(handler, call_type, field):
    transport = field in {"api_key", "headers", "provider_specific_header"}
    data = {"model": "ci-role-alias", field: {"secret": "never-log"}}
    if field == "provider_specific_header":
        data[field] = [{"custom_llm_provider": "anthropic", "extra_headers": {"x-evil": "1", "Anthropic-Beta": "beta"}}]
    body = {"model": data["model"]} if transport else dict(data)
    data["proxy_server_request"] = {"body": body, **({"credential_fields": [field]} if transport else {})}
    out = call(handler, data, call_type)
    if field == "provider_specific_header":
        assert out[field] == [{"custom_llm_provider": "anthropic", "extra_headers": {"Anthropic-Beta": "beta"}}]
    else:
        assert field not in out


@pytest.mark.parametrize("call_type", ["acompletion", "aresponses", "aanthropic_messages"])
def test_missing_credential_fields_fails_closed_for_transport_credentials(handler, call_type):
    data = {"model": "m", "api_key": "secret", "headers": {"x": "y"}, "proxy_server_request": {"body": {"model": "m"}}}
    out = call(handler, data, call_type)
    assert not {"api_key", "headers"} & set(out)


@pytest.mark.parametrize(
    ("call_type", "cross_family_key"),
    [("anthropic_messages", "input"), ("aresponses", "system")],
)
def test_fields_from_other_route_families_are_dropped(handler, call_type, cross_family_key):
    data = {"model": "ci-role-alias", cross_family_key: "client-value"}
    data["proxy_server_request"] = {"body": dict(data)}
    assert cross_family_key not in call(handler, data, call_type)


@pytest.mark.parametrize("call_type", ["acompletion", "aresponses", "aanthropic_messages"])
def test_unknown_keys_are_dropped_and_warned_by_name_only(handler, call_type, caplog):
    data = {
        "model": "ci-role-alias",
        "zz_new": "sensitive-value-should-not-log",
        "aa_new": "another-sensitive-value",
    }
    data["proxy_server_request"] = {"body": dict(data)}
    with caplog.at_level("WARNING", logger="LiteLLM Proxy"):
        out = call(handler, data, call_type)
    assert "zz_new" not in out
    assert "aa_new, zz_new" in caplog.text
    assert "sensitive-value-should-not-log" not in caplog.text
    assert "another-sensitive-value" not in caplog.text


def test_model_fanout_and_bad_snapshots_return_errors(handler):
    data = {"model": "a,b"}
    data["proxy_server_request"] = {"body": dict(data)}
    assert isinstance(call(handler, data, "aresponses"), str)
    assert isinstance(call(handler, {"model": "a"}, "aresponses"), str)
    assert isinstance(call(handler, {"proxy_server_request": {"body": []}}, "aresponses"), str)


def test_live_model_fanout_and_snapshot_clean_model_returns_error(handler):
    data = {"model": "a,b", "proxy_server_request": {"body": {"model": "a"}}}
    assert call(handler, data, "aresponses") == "The CI key must select one model per request."


def test_client_tools_keep_custom_types_and_drop_hosted_types(handler):
    types_to_keep = [None, "function", "custom", "namespace"]
    hosted = [
        "web_search",
        "web_search_20250305",
        "web_fetch_20250910",
        "code_execution_20250825",
        "web_search_preview_2025_03_11",
        "local_shell",
        "web_search_preview",
        "code_interpreter",
        "file_search",
        "mcp",
        "computer_use_preview",
        "image_generation",
    ]
    tools = [
        {"name": f"tool-{index}", **({} if kind is None else {"type": kind})}
        for index, kind in enumerate(types_to_keep + hosted)
    ]
    data = {"model": "ci-role-alias", "tools": tools}
    data["proxy_server_request"] = {"body": dict(data)}
    out = call(handler, data, "aresponses")
    assert [tool.get("type") for tool in out["tools"]] == types_to_keep


@pytest.mark.parametrize("call_type", ["acompletion", "aresponses", "aanthropic_messages"])
def test_versioned_hosted_tools_are_dropped_and_client_tools_kept(handler, call_type):
    keep = [{"name": "untyped"}, {"type": "function"}, {"type": "custom"}, {"type": "namespace"}]
    drop = [{"type": kind} for kind in ("web_search_20250305", "web_fetch_20250910", "code_execution_20250825", "web_search_preview_2025_03_11", "local_shell")]
    tools = keep + drop
    data = {"model": "m", "tools": tools, "proxy_server_request": {"body": {"model": "m", "tools": tools}}}
    assert call(handler, data, call_type)["tools"] == keep


@pytest.mark.parametrize("call_type", ["acompletion", "aresponses", "aanthropic_messages"])
def test_live_router_controls_drop_on_all_families(handler, call_type):
    values = {"additional_drop_params": ["reasoning_effort"], "custom_llm_provider": "x", "_router_weights": {"m": 1}}
    data = {"model": "m", **values, "proxy_server_request": {"body": {"model": "m", **values}}}
    assert not set(values) & set(call(handler, data, call_type))


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
def test_extra_body_keeps_only_chat_template_kwargs_for_every_family(handler, call_type):
    data = {
        "model": "ci-role-alias",
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}, "service_tier": "flex"},
    }
    data["proxy_server_request"] = {"body": dict(data)}
    out = call(handler, data, call_type)
    assert out["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_chat_template_kwargs_drops_nested_effort(handler):
    extra = {"chat_template_kwargs": {"enable_thinking": False, "reasoning_effort": "high"}}
    data = {"model": "m", "extra_body": extra, "proxy_server_request": {"body": {"model": "m", "extra_body": extra}}}
    assert call(handler, data, "acompletion")["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_stream_options_keep_only_include_usage(handler):
    data = {"model": "ci-role-alias", "stream_options": {"include_usage": True, "unknown": True}}
    data["proxy_server_request"] = {"body": dict(data)}
    out = call(handler, data, "acompletion")
    assert out["stream_options"] == {"include_usage": True}
