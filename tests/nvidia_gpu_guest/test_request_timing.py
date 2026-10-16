import asyncio
import json
import logging
from pathlib import Path
from runpy import run_path


ROOT = Path(__file__).resolve().parents[2]


def test_vllm_request_timing_is_wired_without_logging_payloads():
    unit = (ROOT / "roles/vllm_serving/templates/vllm-serving.service.j2").read_text(
        encoding="utf-8"
    )
    tasks = (ROOT / "roles/vllm_serving/tasks/request-timing.yml").read_text(encoding="utf-8")
    middleware = (ROOT / "roles/vllm_serving/files/request_timing.py").read_text(
        encoding="utf-8"
    )

    assert "--middleware request_timing:RequestTimingMiddleware" in unit
    assert "--enable-request-id-headers" in unit
    assert "src: request_timing.py" in tasks
    assert '"event": "vllm_request_timing"' in middleware
    assert '"duration_ms"' in middleware
    assert '"time_to_first_body_ms"' in middleware
    assert '"request_body"' not in middleware
    assert '"response_body"' not in middleware
    assert '"body":' not in middleware


def test_vllm_request_timing_emits_summary_without_response_content(caplog):
    middleware_type = run_path(ROOT / "roles/vllm_serving/files/request_timing.py")[
        "RequestTimingMiddleware"
    ]

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"private response"})

    async def receive():
        return {"type": "http.request", "body": b"private request"}

    async def send(message):
        return None

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"x-request-id", b"request-123")],
    }
    with caplog.at_level(logging.INFO, logger="vllm.request_timing"):
        asyncio.run(middleware_type(app)(scope, receive, send))

    event = json.loads(caplog.records[-1].message)
    assert event["event"] == "vllm_request_timing"
    assert event["request_id"] == "request-123"
    assert event["status_code"] == 200
    assert event["duration_ms"] >= 0
    assert event["time_to_first_body_ms"] is not None
    assert "private request" not in caplog.records[-1].message
    assert "private response" not in caplog.records[-1].message
