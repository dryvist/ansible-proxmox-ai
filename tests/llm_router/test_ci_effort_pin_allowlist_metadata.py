"""Proxy identity and metadata invariants for the CI request allow-list."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "roles/llm_router/files/callbacks/ci_effort_pin.py"
ALIAS = "github-actions"
TAG_FAMILIES = (
    ("acompletion", "metadata", {"messages": []}),
    ("aanthropic_messages", "litellm_metadata", {"messages": [], "metadata": {"user_id": "client-user"}}),
    ("aresponses", "litellm_metadata", {"input": "hello", "metadata": {"client": "metadata"}}),
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


def invoke(handler, data, call_type="aresponses", alias=ALIAS):
    key = types.SimpleNamespace(key_alias=alias)
    return asyncio.run(handler.async_pre_call_hook(key, None, data, call_type))


def snapshot(data):
    data["proxy_server_request"] = {"body": dict(data)}
    return data["proxy_server_request"]["body"]


def pin_tag_metadata(handler, call_type, metadata_key, request_fields, metadata):
    data = {"model": "ci-role-alias", **request_fields, metadata_key: metadata}
    snapshot(data)
    out = invoke(handler, data, call_type)
    return out[metadata_key]


def test_proxy_added_and_replaced_values_survive(handler):
    data = {"model": "ci-role-alias", "messages": []}
    body = snapshot(data)
    replaced = {
        "router_settings_override": {"proxy": True},
        "_router_weights": {"proxy": 1},
        "provider_specific_header": "proxy-header",
        "cache": {"proxy": True},
    }
    client_values = {
        "router_settings_override": {"client": True},
        "_router_weights": {"client": 1},
        "provider_specific_header": "client-header",
        "cache": {"client": True},
    }
    body.update(client_values)
    data.update(replaced)
    extras = {
        "litellm_call_id": "proxy-id",
        "litellm_logging_obj": object(),
        "secret_fields": ["proxy-secret"],
        "dynamic_callback_key": "proxy-value",
        "headers": {"x-proxy": "kept"},
        "metadata": {"user_api_key_alias": "proxy-alias", "headers": {"x-proxy": "kept"}},
        "extra_body": None,
        "stream_options": None,
        "_router_weights": replaced["_router_weights"],
        "router_settings_override": replaced["router_settings_override"],
    }
    data.update(extras)
    out = invoke(handler, data, "acompletion")
    for key, value in extras.items():
        if key in {"headers", "provider_specific_header"}:
            assert key not in out
        else:
            assert out[key] is value
    for key in client_values:
        if key == "provider_specific_header":
            assert key not in out
        else:
            assert out[key] is replaced[key]


def test_client_router_override_and_weights_are_dropped(handler):
    data = {"model": "ci-role-alias", "router_settings_override": {}, "_router_weights": {"m": 1}}
    snapshot(data)
    out = invoke(handler, data)
    assert "router_settings_override" not in out
    assert "_router_weights" not in out


def test_always_kept_fields_survive_from_the_client_snapshot(handler):
    values = {
        "metadata": {"headers": {}, "requester_metadata": {}},
        "litellm_metadata": {"inherited_tags": ["team-tag"], "tags": ["team-tag"]},
        "litellm_trace_id": "trace-id",
        "litellm_session_id": "session-id",
        "drop_params": ["provider-option"],
    }
    data = {"model": "ci-role-alias", "messages": [], **values}
    snapshot(data)
    out = invoke(handler, data, "acompletion")
    assert all(out[key] == value for key, value in values.items())


def test_replaced_litellm_call_id_keeps_the_proxy_value(handler):
    data = {"model": "ci-role-alias", "litellm_call_id": "client-id"}
    body = snapshot(data)
    data["litellm_call_id"] = "proxy-id"
    assert invoke(handler, data)["litellm_call_id"] == "proxy-id"
    assert body["litellm_call_id"] == "client-id"


def test_replaced_responses_metadata_keeps_the_proxy_value(handler):
    data = {"model": "ci-role-alias", "metadata": {"client": "metadata"}}
    body = snapshot(data)
    proxy_metadata = {"user_api_key_alias": "proxy-alias"}
    data["metadata"] = proxy_metadata
    assert invoke(handler, data)["metadata"] is proxy_metadata
    assert body["metadata"] == {"client": "metadata"}


def test_chat_metadata_keeps_proxy_tags_and_headers_only(handler):
    headers = {"user-agent": "client-agent"}
    metadata = {
        "headers": headers,
        "user_id": "client-user",
        "tags": ["client-tag", "team-tag"],
        "inherited_tags": ["team-tag"],
        "caller_tags": ["client-tag", "team-tag"],
        "model_group": "client-group",
        "trace_id": "client-trace",
        "requester_metadata": {
            "headers": headers,
            "user_id": "client-user",
            "tags": ["client-tag"],
            "model_group": "client-group",
            "trace_id": "client-trace",
        },
        "user_api_key_alias": "proxy-alias",
    }
    data = {"model": "ci-role-alias", "messages": [], "metadata": metadata}
    snapshot(data)
    out = invoke(handler, data, "acompletion")
    assert metadata["tags"] == ["team-tag"]
    assert metadata["caller_tags"] == ["team-tag"]
    assert metadata["requester_metadata"] == {
        "headers": headers,
        "user_id": "client-user",
        "model_group": "client-group",
        "trace_id": "client-trace",
    }
    assert metadata["headers"] is headers
    assert metadata["user_id"] == "client-user"
    assert metadata["user_api_key_alias"] == "proxy-alias"
    assert "model_group" not in metadata and "trace_id" not in metadata
    assert out["metadata"] is metadata


def test_chat_without_requester_metadata_does_not_fail(handler):
    metadata = {"tags": ["client-tag"], "inherited_tags": [], "headers": {}}
    data = {"model": "ci-role-alias", "messages": [], "metadata": metadata}
    snapshot(data)
    out = invoke(handler, data, "acompletion")
    assert out["metadata"]["tags"] == []
    assert out["metadata"]["headers"] == {}


def test_tag_pinning_runs_for_metadata_detached_from_snapshot(handler):
    metadata = {"tags": ["client"], "caller_tags": ["client"], "inherited_tags": []}
    data = {"model": "m", "messages": [], "metadata": metadata}
    data["proxy_server_request"] = {"body": {"model": "m", "messages": [], "metadata": {"tags": ["client"]}}}
    out = invoke(handler, data, "acompletion")
    assert out["metadata"]["tags"] == []
    assert out["metadata"]["caller_tags"] == []


def test_anthropic_metadata_keeps_only_user_id_and_inherited_proxy_tags(handler):
    client_metadata = {"user_id": "client-user", "tags": ["client-tag"], "other": "client-value"}
    litellm_metadata = {
        "inherited_tags": ["team-tag"],
        "tags": ["client-tag", "team-tag"],
        "caller_tags": ["client-tag", "team-tag"],
        "requester_metadata": {"tags": ["client-tag"], "user_id": "proxy-copy"},
        "user_api_key_alias": "proxy-alias",
    }
    data = {
        "model": "ci-role-alias",
        "messages": [],
        "metadata": client_metadata,
        "litellm_metadata": litellm_metadata,
    }
    snapshot(data)
    out = invoke(handler, data, "aanthropic_messages")
    assert client_metadata == {"user_id": "client-user"}
    assert litellm_metadata["tags"] == ["team-tag"]
    assert litellm_metadata["caller_tags"] == ["team-tag"]
    assert litellm_metadata["requester_metadata"] == {"user_id": "proxy-copy"}
    assert litellm_metadata["user_api_key_alias"] == "proxy-alias"
    assert out["litellm_metadata"] is litellm_metadata


def test_responses_drops_client_metadata_and_preserves_proxy_tags(handler):
    client_metadata = {"client": "metadata"}
    litellm_metadata = {
        "inherited_tags": ["team-tag"],
        "tags": ["client-tag", "team-tag"],
        "caller_tags": ["client-tag", "team-tag"],
        "user_api_key_alias": "proxy-alias",
    }
    data = {
        "model": "ci-role-alias",
        "input": "hello",
        "metadata": client_metadata,
        "litellm_metadata": litellm_metadata,
    }
    snapshot(data)
    out = invoke(handler, data)
    assert "metadata" not in out
    assert litellm_metadata["tags"] == ["team-tag"]
    assert litellm_metadata["caller_tags"] == ["team-tag"]
    assert litellm_metadata["user_api_key_alias"] == "proxy-alias"


@pytest.mark.parametrize(("call_type", "metadata_key", "request_fields"), TAG_FAMILIES)
def test_tuple_policy_tags_survive_and_client_tags_are_removed(handler, call_type, metadata_key, request_fields):
    metadata = {
        "inherited_tags": ("policy-tag",),
        "tags": ["policy-tag", "client-tag"],
        "caller_tags": ("client-tag",),
    }
    out_metadata = pin_tag_metadata(handler, call_type, metadata_key, request_fields, metadata)
    assert out_metadata["tags"] == ["policy-tag"]
    assert out_metadata["caller_tags"] == ()


@pytest.mark.parametrize(("call_type", "metadata_key", "request_fields"), TAG_FAMILIES)
def test_client_tags_without_inherited_tags_are_removed(handler, call_type, metadata_key, request_fields):
    metadata = {"tags": ["client-tag"], "caller_tags": ("client-tag",)}
    out_metadata = pin_tag_metadata(handler, call_type, metadata_key, request_fields, metadata)
    assert out_metadata["tags"] == []
    assert out_metadata["caller_tags"] == ()


@pytest.mark.parametrize(("call_type", "metadata_key", "request_fields"), TAG_FAMILIES)
def test_tuple_policy_tags_remain_unchanged(handler, call_type, metadata_key, request_fields):
    metadata = {
        "inherited_tags": ("policy-tag",),
        "tags": ["policy-tag"],
        "caller_tags": ("policy-tag",),
    }
    out_metadata = pin_tag_metadata(handler, call_type, metadata_key, request_fields, metadata)
    assert out_metadata["tags"] == ["policy-tag"]
    assert out_metadata["caller_tags"] == ("policy-tag",)


def test_non_ci_keys_are_untouched_and_non_served_ci_route_is_rejected(handler):
    data = {"model": "m", "reasoning_effort": "low", "router_settings_override": {"x": 1}}
    assert invoke(handler, data, alias="open-webui") is data
    result = invoke(handler, {"model": "m"}, "_aresponses_websocket")
    assert result == "The CI key serves chat, Responses and Anthropic messages requests only."
