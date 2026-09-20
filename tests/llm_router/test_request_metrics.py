"""build_record extracts the per-request metrics fields from litellm's own
StandardLoggingPayload and never carries prompt/response content. Loaded
from source without importing litellm (CustomLogger is stubbed), like the
system_merge and lock tests."""

from __future__ import annotations

import ast
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "roles/llm_router/files/callbacks/request_metrics.py"


@pytest.fixture(scope="module")
def module():
    stub = types.ModuleType("litellm.integrations.custom_logger")
    stub.CustomLogger = type("CustomLogger", (), {})
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = stub
    ns: dict = {}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns)  # noqa: S102 - our own file
    return ns


def test_source_parses():
    ast.parse(SOURCE.read_text())


def _kwargs(payload):
    return {"standard_logging_object": payload}


def test_success_record_has_expected_fields(module):
    payload = {
        "model_group": "review-oss",
        "model": "qwen-review",
        "api_base": "http://llm-4080.example.test:8080/v1",
        "status": "success",
        "startTime": 100.0,
        "endTime": 102.5,
        "completionStartTime": 100.4,
        "prompt_tokens": 512,
        "completion_tokens": 128,
        "cache_hit": False,
        "litellm_call_id": "call-123",
        "trace_id": "trace-abc",
        "metadata": {"user_api_key_alias": "ci-reviewer"},
    }
    record = module["build_record"](_kwargs(payload), None, 100.0, 102.5)
    assert record["latency_ms"] == pytest.approx(2500.0)
    assert record["ttft_ms"] == pytest.approx(400.0)
    record.pop("latency_ms")
    record.pop("ttft_ms")
    assert record == {
        "ts": 102.5,
        "event": "llm_request",
        "model_group": "review-oss",
        "model": "qwen-review",
        "api_base": "llm-4080.example.test",
        "status": "success",
        "error_class": None,
        "prompt_tokens": 512,
        "completion_tokens": 128,
        "cache_hit": False,
        "key_alias": "ci-reviewer",
        "call_id": "call-123",
        "trace_id": "trace-abc",
    }


def test_failure_record_carries_error_class(module):
    payload = {
        "model_group": "review-private",
        "model": "hermes-review",
        "status": "failure",
        "startTime": 10.0,
        "endTime": 10.2,
        "error_information": {"error_class": "RateLimitError"},
        "id": "req-9",
        "metadata": {},
    }
    record = module["build_record"](_kwargs(payload), None, 10.0, 10.2)
    assert record["status"] == "failure"
    assert record["error_class"] == "RateLimitError"
    assert record["call_id"] == "req-9"


def test_no_prompt_or_response_content_leaks(module):
    payload = {
        "model_group": "review-oss",
        "model": "qwen-review",
        "status": "success",
        "startTime": 1.0,
        "endTime": 1.1,
        "messages": [{"role": "user", "content": "the actual prompt text"}],
        "response": {"choices": [{"message": {"content": "the actual response text"}}]},
        "metadata": {},
    }
    record = module["build_record"](_kwargs(payload), None, 1.0, 1.1)
    dumped = json.dumps(record)
    assert "messages" not in record
    assert "response" not in record
    assert "the actual prompt text" not in dumped
    assert "the actual response text" not in dumped


def test_missing_api_base_is_none(module):
    payload = {"status": "success", "startTime": 1.0, "endTime": 1.0, "metadata": {}}
    record = module["build_record"](_kwargs(payload), None, 1.0, 1.0)
    assert record["api_base"] is None
    assert record["ttft_ms"] is None


def test_fallback_chain_shares_one_trace_id_across_attempts(module):
    """A fallback chain fires one success/failure event per rung attempted;
    each carries the SAME trace_id but its own call_id — chain depth for one
    request is `stats count by trace_id` on the emitted lines, not a field
    this callback computes itself."""
    first_rung = module["build_record"](
        _kwargs({"status": "failure", "startTime": 1.0, "endTime": 1.05, "trace_id": "t-1", "id": "call-a", "metadata": {}}),
        None,
        1.0,
        1.05,
    )
    second_rung = module["build_record"](
        _kwargs({"status": "success", "startTime": 1.05, "endTime": 1.3, "trace_id": "t-1", "id": "call-b", "metadata": {}}),
        None,
        1.05,
        1.3,
    )
    assert first_rung["trace_id"] == second_rung["trace_id"] == "t-1"
    assert first_rung["call_id"] != second_rung["call_id"]
