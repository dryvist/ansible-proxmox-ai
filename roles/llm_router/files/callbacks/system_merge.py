"""Fold every system/developer message into ONE leading system message.

Registered from litellm_settings.callbacks: "system_merge.proxy_handler_instance"

Why: the local MLX backends (mlx_lm.server) hand the message list straight to
the model's own chat template, and Qwen's template raises
`System message must be at the beginning.` for a second system message or a
system message after the first turn — mlx_lm turns that into a 404, and 404 is
not retried or fallen back from. LiteLLM's Responses API bridge produces exactly
that shape for every Codex turn (`instructions` becomes a system message, the
`developer` item becomes a second one). The Anthropic `/v1/messages` adapter
keeps `system` separate from `messages` until deployment, so merging only the
messages leaves two system messages when the adapter builds the local chat call.

Merging here, in front of every deployment, is what makes the fix hold for
every client and every rung rather than per harness. `async_pre_call_hook`
handles calls that already carry `messages`; `async_pre_call_deployment_hook`
handles the Responses bridge and the Anthropic adapter after deployment
selection. Order is preserved:
system/developer contents are joined in the order they arrived, with two
newlines between them unless a content block carries `cache_control`, in which
case block lists are preserved. Every other message keeps its relative order.
A list with zero or one system-like message and none out of place is returned
untouched.
"""

from litellm.integrations.custom_logger import CustomLogger

_SYSTEMISH = ("system", "developer")


def _as_text(content):
    """Message content is a string or a list of content parts; keep the text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type", "text") == "text"
        )
    return "" if content is None else str(content)


def _merge_contents(contents):
    """Keep cache-control blocks intact; otherwise preserve the legacy text merge."""
    has_cache_control = any(
        isinstance(content, list)
        and any(isinstance(block, dict) and "cache_control" in block for block in content)
        for content in contents
    )
    if not has_cache_control:
        return "\n\n".join(t for t in (_as_text(content) for content in contents) if t)

    merged = []
    for content in contents:
        if isinstance(content, list):
            # The Anthropic API rejects empty text blocks.
            merged.extend(b for b in content if not (isinstance(b, dict) and b.get("type") == "text" and not b.get("text")))
        else:
            text = _as_text(content)
            if text:
                merged.append({"type": "text", "text": text})
    return merged


def merge_system_messages(messages):
    """Return a new message list with one leading system message, or the input as is."""
    if not isinstance(messages, list):
        return messages
    systemish = [m for m in messages if isinstance(m, dict) and m.get("role") in _SYSTEMISH]
    needs_merge = len(systemish) > 1 or (len(systemish) == 1 and messages[0] is not systemish[0])
    if not needs_merge:
        return messages
    merged = _merge_contents([m.get("content") for m in systemish])
    rest = [m for m in messages if not (isinstance(m, dict) and m.get("role") in _SYSTEMISH)]
    return [{"role": "system", "content": merged}] + rest


def merge_anthropic_system(kwargs):
    """Move Anthropic system-role messages into its top-level system field."""
    messages = kwargs.get("messages")
    if not isinstance(messages, list):
        return
    systemish = [m for m in messages if isinstance(m, dict) and m.get("role") in _SYSTEMISH]
    if not systemish:
        return
    contents = ([kwargs["system"]] if kwargs.get("system") is not None else []) + [
        m.get("content") for m in systemish
    ]
    merged = _merge_contents(contents)
    if merged:
        kwargs["system"] = merged
    else:
        kwargs.pop("system", None)
    kwargs["messages"] = [
        m for m in messages if not (isinstance(m, dict) and m.get("role") in _SYSTEMISH)
    ]


class SystemMerge(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        messages = data.get("messages")
        if messages is not None:
            data["messages"] = merge_system_messages(messages)
        return data

    async def async_pre_call_deployment_hook(self, kwargs: dict, call_type):
        # LiteLLM builds Responses messages and adapts Anthropic's top-level
        # system after async_pre_call_hook, so merge the deployment kwargs too.
        if getattr(call_type, "value", call_type) in ("anthropic_messages", "aanthropic_messages"):
            merge_anthropic_system(kwargs)
        else:
            messages = kwargs.get("messages")
            if messages is not None:
                kwargs["messages"] = merge_system_messages(messages)
        return kwargs


proxy_handler_instance = SystemMerge()
