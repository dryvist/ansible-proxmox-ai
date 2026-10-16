"""Emit request timing metadata without recording request or response content."""

import json
import logging
import time


_TIMED_PATHS = {"/v1/chat/completions", "/v1/completions", "/v1/responses"}
_LOGGER = logging.getLogger("vllm.request_timing")


class RequestTimingMiddleware:
    """Log completion timings as one JSON record per supported request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in _TIMED_PATHS:
            await self.app(scope, receive, send)
            return

        started_ns = time.perf_counter_ns()
        timing = {"status_code": None, "request_id": None, "first_body_ns": None}
        for name, value in scope.get("headers", []):
            if name.lower() == b"x-request-id":
                timing["request_id"] = value.decode("utf-8", errors="replace")
                break

        async def send_timed(message):
            if message["type"] == "http.response.start":
                timing["status_code"] = message["status"]
                for name, value in message.get("headers", []):
                    if name.lower() == b"x-request-id":
                        timing["request_id"] = value.decode("utf-8", errors="replace")
                        break
            elif message["type"] == "http.response.body" and message.get("body"):
                if timing["first_body_ns"] is None:
                    timing["first_body_ns"] = time.perf_counter_ns()

            await send(message)

            if message["type"] == "http.response.body" and not message.get("more_body", False):
                ended_ns = time.perf_counter_ns()
                event = {
                    "event": "vllm_request_timing",
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "request_id": timing["request_id"],
                    "status_code": timing["status_code"],
                    "duration_ms": round((ended_ns - started_ns) / 1_000_000, 3),
                    "time_to_first_body_ms": (
                        round((timing["first_body_ns"] - started_ns) / 1_000_000, 3)
                        if timing["first_body_ns"] is not None
                        else None
                    ),
                }
                _LOGGER.info(json.dumps(event, separators=(",", ":"), sort_keys=True))

        await self.app(scope, receive, send_timed)
