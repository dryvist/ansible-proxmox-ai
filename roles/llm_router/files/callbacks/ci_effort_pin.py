"""Keep the CI review key on its three model routes at the effort each deployment declares.

Registered from litellm_settings.callbacks: "ci_effort_pin.proxy_handler_instance"

LiteLLM treats a request-level reasoning_effort as the portable override of a
deployment's own effort, so a request made with the CI key could change the
effort of the CI-only Luna deployment that review-private falls back to. For
the CI key this callback:

- serves chat completions, the Responses API with its compact pass (Codex
  jobs) and the Anthropic messages route (Claude jobs) and rejects every other
  call type, notably the Responses WebSocket, whose frames carry their own
  model and reasoning;
- drops every effort carrier (reasoning_effort, thinking, reasoning,
  output_config), additional_drop_params (which can remove the deployment's own
  effort), client-supplied fallback lists (whose dict entries carry extra
  request fields), router_settings_override (which the router copies
  fallback lists out of when the request has none), custom_llm_provider
  (which would point another provider's login at a local rung) and response_id
  (an unsigned id that makes the compact route pick any deployment);
- keeps only chat_template_kwargs from extra_body (the local rungs' thinking
  toggle), so a Responses body cannot name a replacement model there.

Every other key is untouched. The model is not rewritten here: the key's model
allow-list already bounds which groups, and so which deployments, the key can
name, and a repo test forbids a callback that assigns `model`.
"""

from litellm.integrations.custom_logger import CustomLogger

_CI_KEY_ALIASES = frozenset({"github-actions"})
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
_DROPPED_FIELDS = (
    "reasoning_effort",
    "thinking",
    "reasoning",
    "output_config",
    "additional_drop_params",
    "fallbacks",
    "context_window_fallbacks",
    "content_policy_fallbacks",
    "router_settings_override",
    "_router_weights",
    "custom_llm_provider",
    "response_id",
)
_EXTRA_BODY_KEPT = frozenset({"chat_template_kwargs"})


def pin_request(data):
    """Return the request body without effort carriers, fallback lists or foreign extra_body keys."""
    for field in _DROPPED_FIELDS:
        data.pop(field, None)
    extra_body = data.pop("extra_body", None)
    if isinstance(extra_body, dict):
        kept = {k: v for k, v in extra_body.items() if k in _EXTRA_BODY_KEPT}
        if kept:
            data["extra_body"] = kept
    return data


class CiEffortPin(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        if getattr(user_api_key_dict, "key_alias", None) not in _CI_KEY_ALIASES:
            return data
        if call_type not in _SERVED_CALL_TYPES:
            # A returned string is rejected as a 400 by the proxy.
            return "The CI key serves chat, Responses and Anthropic messages requests only."
        return pin_request(data)


proxy_handler_instance = CiEffortPin()
