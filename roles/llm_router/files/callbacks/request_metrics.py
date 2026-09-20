"""Emit one structured JSON line per request so per-model-group review latency
is measurable from the log platform.

Registered from litellm_settings.callbacks: "request_metrics.proxy_handler_instance"
(config.yaml.j2). LiteLLM's own success/failure logging hooks (below) are the
callback-writer's documented per-request extension point; the JSON line goes
to stdout, which the systemd unit already ships to the journal, forwarded by
the existing llm_router rsyslog route to index=llm, sourcetype=litellm:proxy
(defaults/main/70-syslog.yml) — no new pipeline needed.

Reads litellm's own StandardLoggingPayload (kwargs["standard_logging_object"])
rather than the raw provider response, since that payload is already
normalized across every backend this proxy fronts. NEVER logs prompt/response
content — only the fields named below, all metadata about the call.

`model_group` is the SERVING group, not the one originally asked for: on a
fallback the router overwrites it with the fallback target before the retry
(see docs/LLM_ROUTER_OBSERVABILITY.md, "Identifying a fallback" — verified
there against the pinned litellm distribution). No field survives in kwargs
carrying the pre-fallback group, so it is not fabricated here.

`trace_id` is NOT a per-request depth counter — the earlier draft of this
file used `previous_models` for that, but that list lives on the shared
Router instance (`router.py:714`), not scoped to one request, so it double-
counts under concurrent load. `trace_id` is the one litellm.utils.types.StandardLoggingPayload
field actually scoped per originating call: every rung a fallback chain
attempts logs its own success/failure event sharing the same `trace_id`, so
chain depth for one request is `stats count by trace_id` in Splunk, not a
field this callback needs to compute.
"""

import json
from urllib.parse import urlparse

from litellm.integrations.custom_logger import CustomLogger


def _host_only(api_base):
    """api_base may carry a full URL; keep the host, never the path/query."""
    if not api_base:
        return None
    return urlparse(api_base).hostname or api_base


def build_record(kwargs, response_obj, start_time, end_time):
    """Pure function so it's testable without a real litellm proxy or hooks."""
    payload = kwargs.get("standard_logging_object") or {}
    metadata = payload.get("metadata") or {}
    error_info = payload.get("error_information") or {}

    start = payload.get("startTime")
    end = payload.get("endTime")
    latency_ms = (end - start) * 1000 if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
    completion_start = payload.get("completionStartTime")
    ttft_ms = (
        (completion_start - start) * 1000
        if isinstance(completion_start, (int, float)) and isinstance(start, (int, float)) and completion_start > start
        else None
    )

    return {
        "ts": end if isinstance(end, (int, float)) else start,
        "event": "llm_request",
        "model_group": payload.get("model_group"),
        "model": payload.get("model"),
        "api_base": _host_only(payload.get("api_base")),
        "status": payload.get("status"),
        "error_class": error_info.get("error_class"),
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms,
        "prompt_tokens": payload.get("prompt_tokens"),
        "completion_tokens": payload.get("completion_tokens"),
        "cache_hit": payload.get("cache_hit"),
        "key_alias": metadata.get("user_api_key_alias"),
        "call_id": payload.get("litellm_call_id") or payload.get("id"),
        "trace_id": payload.get("trace_id"),
    }


class RequestMetrics(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        print(json.dumps(build_record(kwargs, response_obj, start_time, end_time)), flush=True)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        print(json.dumps(build_record(kwargs, response_obj, start_time, end_time)), flush=True)


proxy_handler_instance = RequestMetrics()
