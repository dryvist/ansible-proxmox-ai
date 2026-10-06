from __future__ import annotations

import logging
import json
import threading
from types import SimpleNamespace
from typing import Any, Dict

from conftest import PATCHED_HINDSIGHT_FAILURE_SOURCE, PINNED_HINDSIGHT_FAILURE_SOURCE


def _provider(source: str, client: Any):
    namespace = {
        "Dict": Dict,
        "_RETRIABLE_CONNECTION_MARKERS": ("connection refused", "connection reset"),
        "logger": logging.getLogger("test.hindsight.failure"),
        "json": json,
        "tool_error": lambda message: f"tool_error:{message}",
    }
    exec(source, namespace)  # noqa: S102 - execute the pinned upstream fixture
    provider = namespace["HindsightMemoryProvider"]()
    provider._client = client
    provider._mode = "local_external"
    provider._run_sync = lambda operation: operation
    provider._get_client = lambda: provider._client
    provider._prefetch_lock = threading.Lock()
    provider._prefetch_thread = None
    provider._prefetch_result = ""
    provider._prefetch_count = 0
    provider._pending_hindsight_failure_ids = []
    provider._status_callback = None
    provider._recall_sync = True
    provider._recall_max_input_chars = 800
    provider._prefetch_method = "recall"
    provider._bank_id = "test-bank"
    provider._budget = "mid"
    provider._recall_max_tokens = 256
    provider._recall_tags = None
    provider._recall_types = ["observation"]
    provider._recall_prompt_preamble = ""
    return provider


def test_auto_recall_failure_is_counted_and_reaches_the_next_turn(caplog) -> None:
    class FailedRecallClient:
        def arecall(self, **kwargs):
            raise RuntimeError("private query text must not enter the failure log")

    logger_name = "test.hindsight.failure"
    with caplog.at_level(logging.ERROR, logger=logger_name):
        provider = _provider(PATCHED_HINDSIGHT_FAILURE_SOURCE, FailedRecallClient())
        context = provider.prefetch("private query text")

    assert context.startswith("[HINDSIGHT_MEMORY_DEGRADED degraded=true request_ids=")
    request_id = context.split("request_ids=", 1)[1].split(" ", 1)[0]
    assert request_id
    assert f"request_id={request_id}" in caplog.text
    assert "hindsight_failure_count=1" in caplog.text
    assert "hindsight_degraded=true" in caplog.text
    assert "private query text" not in caplog.text
    assert provider.recall_status() is None


def test_pinned_upstream_reproduces_the_empty_success_appearance() -> None:
    class FailedRecallClient:
        def arecall(self, **kwargs):
            raise RuntimeError("service unavailable")

    provider = _provider(PINNED_HINDSIGHT_FAILURE_SOURCE, FailedRecallClient())
    assert provider.prefetch("query") == ""


def test_sync_prefetch_dispatches_one_recall_for_the_current_query() -> None:
    class StoredFactClient:
        def __init__(self) -> None:
            self.calls = []

        def arecall(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(results=[SimpleNamespace(text="stored fixture fact")])

    client = StoredFactClient()
    provider = _provider(PINNED_HINDSIGHT_FAILURE_SOURCE, client)
    query = "What is the stored fixture fact?"

    context = provider.prefetch(query)

    assert len(client.calls) == 1
    assert client.calls[0]["query"] == query
    assert "stored fixture fact" in context


def test_async_retain_failure_after_acceptance_is_reported_and_marked(caplog) -> None:
    class FailedStatusClient:
        class operations:
            @staticmethod
            def get_operation_status(**kwargs):
                return SimpleNamespace(status="failed")

        def arecall(self, **kwargs):
            return SimpleNamespace(results=[])

    logger_name = "test.hindsight.failure"
    with caplog.at_level(logging.ERROR, logger=logger_name):
        provider = _provider(PATCHED_HINDSIGHT_FAILURE_SOURCE, FailedStatusClient())
        assert provider._is_retain_op_complete("test-bank", "operation-123") is True
        context = provider.prefetch("next turn")

    assert "operation=retain" in caplog.text
    assert "hindsight_failure_count=1" in caplog.text
    assert context.startswith("[HINDSIGHT_MEMORY_DEGRADED degraded=true request_ids=")
    assert "failure_count=1" in context


def test_recall_result_formatting_failure_is_not_silenced(caplog) -> None:
    class InvalidResult:
        @property
        def text(self):
            raise ValueError("invalid Hindsight recall result")

    class MalformedRecallClient:
        def arecall(self, **kwargs):
            return SimpleNamespace(results=[InvalidResult()])

    logger_name = "test.hindsight.failure"
    with caplog.at_level(logging.ERROR, logger=logger_name):
        provider = _provider(PATCHED_HINDSIGHT_FAILURE_SOURCE, MalformedRecallClient())
        context = provider.prefetch("query")

    assert context.startswith("[HINDSIGHT_MEMORY_DEGRADED degraded=true request_ids=")
    assert "error_type=ValueError" in caplog.text
    assert "hindsight_failure_count=1" in caplog.text


def test_retain_transport_failure_preserves_error_and_request_id(caplog) -> None:
    class FailedRetainClient:
        def aretain_batch(self, **kwargs):
            raise RuntimeError("private retained content must not enter the failure log")

    logger_name = "test.hindsight.failure"
    with caplog.at_level(logging.ERROR, logger=logger_name):
        provider = _provider(PATCHED_HINDSIGHT_FAILURE_SOURCE, FailedRetainClient())
        try:
            provider._retain_batch({"content": "private"}, bank_id="test-bank")
        except RuntimeError as exc:
            request_id = exc.hindsight_request_id
        else:
            raise AssertionError("retain failure was swallowed")

    assert request_id in caplog.text
    assert "operation=retain" in caplog.text
    assert "hindsight_failure_count=1" in caplog.text
    assert "private retained content" not in caplog.text


def test_explicit_tool_failure_returns_the_central_request_id() -> None:
    class FailedRetainClient:
        def aretain_batch(self, **kwargs):
            raise RuntimeError("private retained content must not enter the tool error")

    provider = _provider(PATCHED_HINDSIGHT_FAILURE_SOURCE, FailedRetainClient())
    result = provider.handle_tool_call(
        "hindsight_retain",
        {},
        lambda instance, args: instance._retain_batch(
            {"content": "private"}, bank_id="test-bank"
        ),
        "Failed to store memory",
    )

    assert result.startswith("tool_error:Failed to store memory (request_id=")
    assert "private retained content" not in result
