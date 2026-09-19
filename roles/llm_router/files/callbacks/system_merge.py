"""Fold every system/developer message into ONE leading system message.

Registered from litellm_settings.callbacks: "system_merge.proxy_handler_instance"

Why: the local MLX backends (mlx_lm.server) hand the message list straight to
the model's own chat template, and Qwen's template raises
`System message must be at the beginning.` for a second system message or a
system message after the first turn — mlx_lm turns that into a 404, and 404 is
not retried or fallen back from. LiteLLM's Responses API bridge produces exactly
that shape for every Codex turn (`instructions` becomes a system message, the
`developer` item becomes a second one), so every Responses-API caller on an
MLX-backed role failed outright.

Merging here, once, in front of every deployment, is what makes the fix hold
for every client and every rung rather than per harness. Order is preserved:
system/developer texts are joined in the order they arrived, with two newlines
between them; every other message keeps its relative order. A list with zero
or one system-like message and none out of place is returned untouched.
"""

from litellm.integrations.custom_logger import CustomLogger

_SYSTEMISH = frozenset({"system", "developer"})


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


def merge_system_messages(messages):
    """Return a new message list with one leading system message, or the input as is."""
    if not isinstance(messages, list):
        return messages
    systemish = [m for m in messages if isinstance(m, dict) and m.get("role") in _SYSTEMISH]
    needs_merge = len(systemish) > 1 or (len(systemish) == 1 and messages[0] is not systemish[0])
    if not needs_merge:
        return messages
    merged = "\n\n".join(t for t in (_as_text(m.get("content")) for m in systemish) if t)
    rest = [m for m in messages if not (isinstance(m, dict) and m.get("role") in _SYSTEMISH)]
    return [{"role": "system", "content": merged}] + rest


class SystemMerge(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        messages = data.get("messages")
        if messages is not None:
            data["messages"] = merge_system_messages(messages)
        return data


proxy_handler_instance = SystemMerge()
