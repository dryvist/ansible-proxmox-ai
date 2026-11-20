"""Hermes cron metadata becomes only bounded, authenticated request tags."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CALLBACK = REPO_ROOT / "roles/llm_router/files/callbacks/hermes_job_tags.py"
JOB_TAGS = {
    "daily-summary": "telemetry_job_daily_summary",
    "mixed.job/name": "telemetry_job_mixed_job_name",
}


@pytest.fixture
def handler(monkeypatch):
    encoded = base64.b64encode(json.dumps(JOB_TAGS).encode()).decode()
    monkeypatch.setenv("LLM_ROUTER_HERMES_JOB_TAG_MAP_B64", encoded)
    litellm = types.ModuleType("litellm")
    litellm.__path__ = []
    integrations = types.ModuleType("litellm.integrations")
    integrations.__path__ = []
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.integrations", integrations)
    monkeypatch.setitem(sys.modules, "litellm.integrations.custom_logger", custom_logger)
    namespace = {"__name__": "hermes_job_tags_test"}
    exec(compile(CALLBACK.read_text(), str(CALLBACK), "exec"), namespace)  # noqa: S102 - callback source under test
    return namespace["proxy_handler_instance"]


def _key(alias: str):
    return types.SimpleNamespace(key_alias=alias)


def _apply(handler, key, data):
    return asyncio.run(
        handler.async_pre_call_hook(
            user_api_key_dict=key,
            cache=None,
            data=data,
            call_type="completion",
        )
    )


def test_allowed_job_name_adds_its_declared_tag_and_preserves_other_metadata(handler):
    request = {"metadata": {"hermes_job": "daily-summary", "runner": "cron", "tags": ["existing"]}}

    result = _apply(handler, _key("hermes-example"), request)

    assert result["metadata"] == {
        "hermes_job": "daily-summary",
        "runner": "cron",
        "tags": ["existing", "telemetry_job_daily_summary"],
    }
    assert request["metadata"]["tags"] == ["existing"]


def test_unknown_nonempty_job_name_uses_other_bucket(handler):
    result = _apply(handler, _key("hermes-github-maint"), {"metadata": {"hermes_job": "unlisted-job"}})

    assert result["metadata"]["tags"] == ["telemetry_job_other"]


def test_hermes_key_without_job_metadata_uses_aux_bucket(handler):
    result = _apply(handler, _key("hermes-example"), {"model": "example-model"})

    assert result["metadata"]["tags"] == ["telemetry_job_aux"]


def test_non_hermes_key_is_unchanged_even_with_job_metadata(handler):
    request = {"metadata": {"hermes_job": "daily-summary"}}

    assert _apply(handler, _key("openwebui"), request) is request
    assert request == {"metadata": {"hermes_job": "daily-summary"}}


@pytest.mark.parametrize(
    "metadata",
    [None, "malformed", {"hermes_job": 23}, {"hermes_job": "daily-summary", "tags": "bad"}],
)
def test_malformed_metadata_is_left_untouched(handler, metadata):
    request = {"metadata": metadata}

    assert _apply(handler, _key("hermes-example"), request) is request
    assert request == {"metadata": metadata}


def test_label_normalization_fixture_is_prometheus_safe(handler):
    result = _apply(handler, _key("hermes-example"), {"metadata": {"hermes_job": "mixed.job/name"}})

    assert result["metadata"]["tags"] == ["telemetry_job_mixed_job_name"]
