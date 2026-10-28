"""Add a response header for requests that use a router lane name."""

import os

from litellm.integrations.custom_logger import CustomLogger

_LANE_NAMES = frozenset(
    lane.strip()
    for lane in os.environ.get("LLM_ROUTER_LANE_NAMES", "").split(",")
    if lane.strip()
)


def _lane_header(data):
    model = data.get("model") if isinstance(data, dict) else None
    if isinstance(model, str) and model in _LANE_NAMES:
        return {"X-LiteLLM-Lane": model}
    return None


class LaneHeader(CustomLogger):
    async def async_post_call_response_headers_hook(
        self,
        data: dict,
        user_api_key_dict,
        response,
        request_headers: dict[str, str] | None = None,
        litellm_call_info: dict[str, object] | None = None,
    ):
        return _lane_header(data)


proxy_handler_instance = LaneHeader()
