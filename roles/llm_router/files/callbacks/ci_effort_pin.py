"""Pin the CI key to fields needed by the served client routes."""

import logging

from litellm.integrations.custom_logger import CustomLogger

_CI_KEY_ALIASES = frozenset({"github-actions"})
_THINKING_OFF_ALIASES = _CI_KEY_ALIASES | {"github-actions-oss"}
_THINKING_OFF_MODELS = frozenset({"review-local", "review-private", "review-oss"})
_SERVED_CALL_TYPES = frozenset(
    {
        "completion",
        "acompletion",
        "responses",
        "aresponses",
        "compact_responses",
        "acompact_responses",
        "anthropic_messages",
        "aanthropic_messages",
    }
)
_ROUTE_FAMILIES = {
    "completion": "chat",
    "acompletion": "chat",
    "responses": "responses",
    "aresponses": "responses",
    "compact_responses": "responses",
    "acompact_responses": "responses",
    "anthropic_messages": "anthropic",
    "aanthropic_messages": "anthropic",
}
_ALLOWED_FIELDS = {
    "chat": frozenset(
        {
            "model",
            "messages",
            "temperature",
            "top_p",
            "max_tokens",
            "max_completion_tokens",
            "seed",
            "user",
            "stream",
            "stream_options",
            "response_format",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "stop",
            "extra_body",
        }
    ),
    "responses": frozenset(
        {
            "model",
            "input",
            "instructions",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "include",
            "store",
            "stream",
            "prompt_cache_key",
            "client_metadata",
            "max_output_tokens",
            "temperature",
            "user",
        }
    ),
    "anthropic": frozenset(
        {
            "model",
            "messages",
            "system",
            "max_tokens",
            "stream",
            "tools",
            "tool_choice",
            "temperature",
            "top_p",
            "top_k",
            "stop_sequences",
            "context_management",
            "safeguards",
        }
    ),
}
_ALWAYS_KEPT = frozenset(
    {"metadata", "litellm_metadata", "litellm_trace_id", "litellm_session_id", "drop_params"}
)
_KNOWN_CARRIERS = frozenset(
    {
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
    }
)
_CLIENT_TOOL_TYPES = frozenset({"function", "custom", "namespace"})
_EXTRA_BODY_KEPT = frozenset({"chat_template_kwargs"})
_PROVIDER_HEADERS = frozenset({"anthropic-version", "anthropic-beta"})
# LiteLLM copies these from x-litellm-* request headers after the body snapshot, so the snapshot never lists them.
_HEADER_CARRIERS = ("num_retries", "timeout", "stream_timeout", "keepalive_seconds", "client_side_timeout")
_LOG = logging.getLogger("LiteLLM Proxy")
_INVALID_SNAPSHOT = "The CI key request body snapshot is missing or invalid."


def _inherited_tags(metadata):
    inherited = metadata.get("inherited_tags", [])
    return inherited if isinstance(inherited, (list, tuple)) else []


def _pin_tags(metadata):
    inherited = _inherited_tags(metadata)
    for field in ("tags", "caller_tags"):
        tags = metadata.get(field)
        if isinstance(tags, (list, tuple)):
            # Keep only tags inherited from proxy key/team policy; client tags are routing hints.
            kept = [tag for tag in tags if tag in inherited]
            metadata[field] = kept if field == "tags" or isinstance(tags, list) else tuple(kept)
    requester = metadata.get("requester_metadata")
    if isinstance(requester, dict):
        requester.pop("tags", None)


def _pin_metadata(data, body, family):
    metadata = data.get("metadata")
    if "metadata" not in body or metadata is not body["metadata"] or not isinstance(metadata, dict):
        return
    if family == "anthropic":
        for key in list(metadata):
            if key != "user_id":
                metadata.pop(key)
        return
    if family == "responses":
        data.pop("metadata", None)
        return

    had_tags = isinstance(metadata.get("tags"), (list, tuple))
    kept_tags = [tag for tag in metadata["tags"] if tag in _inherited_tags(metadata)] if had_tags else None
    client = metadata.get("requester_metadata")
    if isinstance(client, dict):
        for key, value in client.items():
            if key not in {"headers", "user_id"} and metadata.get(key) == value:
                metadata.pop(key, None)
    _pin_tags(metadata)
    if had_tags:
        metadata["tags"] = kept_tags


def _sanitize_client_fields(data, body, allowed):
    if "extra_body" in body:
        extra_body = data.get("extra_body")
        if isinstance(extra_body, dict):
            kept = {key: value for key, value in extra_body.items() if key in _EXTRA_BODY_KEPT}
            template = kept.get("chat_template_kwargs")
            if isinstance(template, dict):
                template = {
                    key: value
                    for key, value in template.items()
                    if key == "enable_thinking" and isinstance(value, bool)
                }
                if template:
                    kept["chat_template_kwargs"] = template
                else:
                    kept.pop("chat_template_kwargs", None)
            else:
                kept.pop("chat_template_kwargs", None)
            if kept:
                data["extra_body"] = kept
            else:
                data.pop("extra_body", None)
        else:
            data.pop("extra_body", None)
    if "stream_options" in body and "stream_options" in allowed:
        options = data.get("stream_options")
        kept = (
            {"include_usage": options["include_usage"]}
            if isinstance(options, dict) and "include_usage" in options
            else {}
        )
        if kept:
            data["stream_options"] = kept
        else:
            data.pop("stream_options", None)
    if "tools" in allowed and "tools" in body and data.get("tools") is not None:
        tools = data["tools"]
        if isinstance(tools, list):
            # Keep only client-defined tool types and untyped tools.
            data["tools"] = [
                tool
                for tool in tools
                if not isinstance(tool, dict)
                or "type" not in tool
                or (isinstance(tool["type"], str) and tool["type"] in _CLIENT_TOOL_TYPES)
            ]
        else:
            data.pop("tools", None)


def _sanitize_transport_fields(data, request):
    names = request.get("credential_fields")
    if not isinstance(names, (tuple, list)):
        names = ("api_key", "headers", "provider_specific_header")
    for name in names:
        if name in {"api_key", "headers"}:
            data.pop(name, None)
    value = data.get("provider_specific_header")
    if value is None:
        return
    entries = value if isinstance(value, (tuple, list)) else [value]
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("extra_headers"), dict):
            data.pop("provider_specific_header", None)
            return
        headers = {
            k: v for k, v in entry["extra_headers"].items() if isinstance(k, str) and k.lower() in _PROVIDER_HEADERS
        }
        if headers:
            cleaned.append({**entry, "extra_headers": headers})
    if cleaned:
        data["provider_specific_header"] = cleaned if isinstance(value, (tuple, list)) else cleaned[0]
    else:
        data.pop("provider_specific_header", None)


def pin_request(data, call_type, body):
    family = _ROUTE_FAMILIES[call_type]
    allowed = _ALLOWED_FIELDS[family] | _ALWAYS_KEPT
    unknown = []
    for key in body:
        if key not in allowed and key != "extra_body" and data.get(key) is body[key]:
            data.pop(key, None)
            if key not in _KNOWN_CARRIERS:
                unknown.append(key)
    if unknown:
        _LOG.warning(
            "CI key alias %s call type %s dropped unknown request keys: %s",
            "github-actions",
            call_type,
            ", ".join(sorted(unknown)),
        )
    _sanitize_client_fields(data, body, _ALLOWED_FIELDS[family])
    _pin_metadata(data, body, family)
    for key in ("metadata", "litellm_metadata"):
        value = data.get(key)
        if isinstance(value, dict):
            _pin_tags(value)
    return data


def default_thinking_off(data, call_type):
    model = data.get("model")
    if _ROUTE_FAMILIES.get(call_type) != "chat" or not isinstance(model, str) or model not in _THINKING_OFF_MODELS:
        return
    extra = data.setdefault("extra_body", {}) if data.get("extra_body") is None else data["extra_body"]
    template = extra.get("chat_template_kwargs") if isinstance(extra, dict) else False
    if template is None:
        template = extra["chat_template_kwargs"] = {}
    if isinstance(template, dict):
        template.setdefault("enable_thinking", False)


class CiEffortPin(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        alias = getattr(user_api_key_dict, "key_alias", None)
        if alias not in _CI_KEY_ALIASES:
            if alias in _THINKING_OFF_ALIASES:
                default_thinking_off(data, call_type)
            return data
        if call_type not in _SERVED_CALL_TYPES:
            return "The CI key serves chat, Responses and Anthropic messages requests only."
        request = data.get("proxy_server_request")
        body = request.get("body") if isinstance(request, dict) else None
        if not isinstance(body, dict):
            return _INVALID_SNAPSHOT
        _sanitize_transport_fields(data, request)
        for name in _HEADER_CARRIERS:
            data.pop(name, None)
        for model in (body.get("model"), data.get("model")):
            if model is not None and (not isinstance(model, str) or "," in model):
                return "The CI key must select one model per request."
        pinned = pin_request(data, call_type, body)
        default_thinking_off(pinned, call_type)
        return pinned


proxy_handler_instance = CiEffortPin()
