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
    fastapi = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail):
            self.status_code, self.detail = status_code, detail

    fastapi.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi
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
        "api_base": "http://backend.example.test:8080/v1",
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
        "api_base": "backend.example.test",
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


def test_key_defaults_fill_all_carriers_without_changing_routing(module):
    data = {"model": "test-target", "priority": 5, "metadata": {"client": "spoofed", "secret": "never log"}}
    key = {"trace_defaults": {"client": "consumer", "runner": "runner", "runtime": "runner",
                             "purpose": "live", "tier": "test-tier", "environment": "production", "release": "test"}}
    result = module["apply_trace_contract"](data, key)
    metadata = result["metadata"]
    assert result["model"] == "test-target" and result["priority"] == 5
    assert metadata["client"] == "consumer" and metadata["session_id"]
    assert result["user"] == "consumer"
    for carrier in ["requester_metadata", "spend_logs_metadata", "trace_metadata"]:
        assert metadata[carrier]["runner"] == "runner"
        assert metadata[carrier]["session_id"] == metadata["session_id"]
    record = module["build_record"](_kwargs({"metadata": metadata}), None, 0, 1)
    assert record["purpose"] == "live" and "secret" not in record


def test_benchmark_requires_variables_and_accepts_false_and_zero(module):
    key = {"trace_defaults": {"client": "eval", "runner": "eval", "purpose": "benchmark"},
           "trace_required": ["run_id", "thinking", "power_limit"]}
    with pytest.raises(module["HTTPException"]) as exc:
        module["apply_trace_contract"]({"metadata": {"run_id": "run-1"}}, key)
    assert exc.value.status_code == 400
    assert "thinking" in exc.value.detail and "power_limit" in exc.value.detail
    data = {"litellm_metadata": {"run_id": "run-1", "thinking": False, "power_limit": 0}}
    result = module["apply_trace_contract"](data, key)
    assert result["litellm_metadata"]["trace_metadata"]["thinking"] is False
    assert result["litellm_metadata"]["session_id"] == "run-1"
    assert "metadata" not in result


def test_caller_session_and_attribution_survive_defaults(module):
    key = {
        "trace_defaults": {
            "client": "consumer",
            "runner": "default-runner",
            "purpose": "benchmark",
            "tier": "local",
        },
        "trace_required": ["run_id"],
    }
    data = {
        "user": "person",
        "litellm_session_id": "session",
        "metadata": {
            "runner": "lm-eval",
            "purpose": "live",
            "tier": "large",
            "run_id": "run-042",
            "trace_name": "custom",
            "trace_release": "app-release",
            "trace_version": "component-version",
        },
    }
    result = module["apply_trace_contract"](data, key)
    assert result["user"] == "person"
    assert result["metadata"]["session_id"] == "session"
    assert result["metadata"]["trace_user_id"] == "person"
    assert result["metadata"]["trace_name"] == "custom"
    for carrier in ["requester_metadata", "spend_logs_metadata", "trace_metadata"]:
        assert result["metadata"][carrier]["runner"] == "lm-eval"
        assert result["metadata"][carrier]["purpose"] == "live"
        assert result["metadata"][carrier]["tier"] == "large"
        assert result["metadata"][carrier]["trace_release"] == "app-release"
        assert result["metadata"][carrier]["trace_version"] == "component-version"
    record = module["build_record"](_kwargs({"metadata": result["metadata"]}), None, 0, 1)
    assert record["runner"] == "lm-eval"
    assert record["purpose"] == "live"
    assert record["tier"] == "large"


@pytest.mark.parametrize("key_metadata", [None, {}, {"trace_defaults": {}}])
def test_unseeded_key_gets_unattributed_defaults(module, key_metadata):
    result = module["apply_trace_contract"]({"model": "test-target"}, key_metadata)
    metadata = result["metadata"]
    assert result["model"] == "test-target"
    assert metadata["client"] == metadata["runner"] == metadata["purpose"] == "unattributed"
    assert metadata["requester_metadata"]["purpose"] == "unattributed"
    assert result["user"] == "unattributed"
    assert metadata["session_id"]


def test_unseeded_key_fallback_client_is_key_alias(module):
    result = module["apply_trace_contract"]({"model": "test-target"}, None, key_alias="langgraph")
    assert result["metadata"]["client"] == "langgraph"
    assert result["user"] == "langgraph"


def test_blank_identity_fields_fill_and_tools_reach_carriers(module):
    key = {"trace_defaults": {"client": "consumer", "runner": "eval", "purpose": "live", "release": "test"}}
    data = {"user": None, "metadata": {"trace_user_id": "", "trace_name": "", "generation_name": None,
                                      "trace_release": None, "trace_version": "",
                                      "tool_category": "search", "tool_name": "lookup"}}
    result = module["apply_trace_contract"](data, key)
    assert result["user"] == "consumer"
    for field in ["trace_user_id", "trace_name", "generation_name", "trace_release", "trace_version"]:
        assert result["metadata"][field]
    for carrier in ["requester_metadata", "spend_logs_metadata", "trace_metadata"]:
        assert result["metadata"][carrier]["tool_name"] == "lookup"
        assert result["metadata"][carrier]["tool_category"] == "search"
    assert module["RequestMetrics"]().enforces_request_content is True


def test_benchmark_rejects_nonscalar_labels(module):
    key = {"trace_defaults": {"client": "eval", "runner": "eval", "purpose": "benchmark"},
           "trace_required": ["run_id"]}
    with pytest.raises(module["HTTPException"]):
        module["apply_trace_contract"]({"metadata": {"run_id": []}}, key)


@pytest.mark.parametrize("required", [None, []])
def test_benchmark_without_declared_requirements_is_rejected(module, required):
    key = {"trace_defaults": {"client": "eval", "runner": "eval", "purpose": "benchmark"},
           "trace_required": required}
    with pytest.raises(module["HTTPException"]) as exc:
        module["apply_trace_contract"]({"metadata": {"run_id": "run-1"}}, key)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Missing benchmark trace requirements"
