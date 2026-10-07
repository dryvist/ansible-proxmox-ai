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
from uuid import uuid4

from fastapi import HTTPException

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

    record = {
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
    # Emit only the explicit contract, never arbitrary metadata or credentials.
    contract = metadata.get("requester_metadata") or {}
    safe_fields = (
        "client", "runtime", "runner", "purpose", "tier", "environment", "release",
        "session_id", "trace_user_id", "trace_name", "generation_name", "tool_category", "tool_name",
        "engine", "profile", "quant", "kv_dtype", "ctx_per_slot", "slots", "concurrency",
        "power_limit", "thinking", "suite", "dataset", "run_id",
    )
    record.update({field: contract[field] for field in safe_fields if field in contract})
    return record


def apply_trace_contract(data, key_metadata):
    """Fill key-owned attribution and reject unlabelled benchmark calls."""
    defaults = (key_metadata or {}).get("trace_defaults") or {}
    if not defaults:
        raise HTTPException(status_code=400, detail="Missing consumer trace defaults")
    slot = "litellm_metadata" if "litellm_metadata" in data else "metadata"
    metadata = data.setdefault(slot, {})
    metadata.update(defaults)
    session = data.get("litellm_session_id") or metadata.get("session_id")
    metadata["session_id"] = session or metadata.get("run_id") or data.get("litellm_trace_id") or metadata.get("trace_id") or uuid4().hex
    for field, fallback in {
        "trace_user_id": data.get("user") or defaults["client"],
        "trace_name": defaults["runner"] + "/" + defaults["purpose"],
        "trace_release": defaults.get("release"),
        "trace_version": defaults.get("release"),
    }.items():
        if metadata.get(field) is None or metadata.get(field) == "":
            metadata[field] = fallback
    if not metadata.get("generation_name"):
        metadata["generation_name"] = metadata["trace_name"]
    if not data.get("user"):
        data["user"] = metadata["trace_user_id"]
    required = (key_metadata or {}).get("trace_required") or []
    if defaults.get("purpose") == "benchmark":
        if not required:
            raise HTTPException(status_code=400, detail="Missing benchmark trace requirements")
        missing = [field for field in required
                   if not isinstance(metadata.get(field), (str, int, float, bool)) or metadata[field] == ""]
        if missing:
            raise HTTPException(status_code=400, detail="Missing benchmark metadata: " + ", ".join(missing))
    fields = list(defaults) + ["session_id", "trace_user_id", "trace_name", "generation_name",
                               "tool_category", "tool_name"] + required
    contract = {field: metadata[field] for field in fields if metadata.get(field) is not None}
    metadata["requester_metadata"] = {**(metadata.get("requester_metadata") or {}), **contract}
    metadata["spend_logs_metadata"] = {**(metadata.get("spend_logs_metadata") or {}), **contract}
    metadata["trace_metadata"] = {**(metadata.get("trace_metadata") or {}), **contract}
    return data


class RequestMetrics(CustomLogger):
    # Validate individual records on gateway batch-content scanning paths too.
    enforces_request_content = True

    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        return apply_trace_contract(data, user_api_key_dict.metadata)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        print(json.dumps(build_record(kwargs, response_obj, start_time, end_time)), flush=True)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        print(json.dumps(build_record(kwargs, response_obj, start_time, end_time)), flush=True)


proxy_handler_instance = RequestMetrics()
