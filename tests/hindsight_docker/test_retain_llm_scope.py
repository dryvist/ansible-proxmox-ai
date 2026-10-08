"""Hindsight's background retain (fact extraction) scope must render onto the
fabric's light `cheap` role, at concurrency 1, with a bounded retry budget —
not the fabric-wide primary/master-key defaults every other LLM call here
uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from _compose_render import env_line, render

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hindsight_docker/templates/docker-compose.yml.j2"
LIVE_FIXTURE = REPO_ROOT / "tests/hindsight_docker/fixtures/live-retain-observations.json"


def test_retain_scope_uses_the_dedicated_primary_route_alias() -> None:
    rendered = render()
    assert '"qwen3.8-27b"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MODEL")
    assert '"qwen3.8-27b"' in env_line(rendered, "HINDSIGHT_API_LLM_MODEL")
    inventory = (REPO_ROOT / "inventory/group_vars/all.yml").read_text()
    assert "hindsight_retain_model: qwen3.8-27b" in inventory


def test_retain_scope_admits_one_in_flight_extraction() -> None:
    rendered = render()
    assert '"1"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT")


def test_retain_retries_are_bounded_and_jittered() -> None:
    rendered = render()
    assert '"2"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES")
    assert '"30.0"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_INITIAL_BACKOFF")
    assert '"120.0"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MAX_BACKOFF")
    assert '"1"' in env_line(rendered, "HINDSIGHT_API_WORKER_MAX_RETRIES")
    assert '"60"' in env_line(
        rendered, "HINDSIGHT_API_WORKER_TASK_RETRY_BACKOFF_SECONDS"
    )


def test_retain_limits_cover_real_target_observations() -> None:
    observed = json.loads(LIVE_FIXTURE.read_text())
    replicas = observed["replicas"]
    bank = observed["bank_config"]
    rendered = render()

    output_cap = int(
        env_line(rendered, "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS").split(":", 1)[1]
        .strip()
        .strip('"')
    )
    request_timeout = int(
        env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_TIMEOUT").split(":", 1)[1]
        .strip()
        .strip('"')
    )
    wall_timeout = int(
        env_line(rendered, "HINDSIGHT_API_RETAIN_WALL_TIMEOUT").split(":", 1)[1]
        .strip()
        .strip('"')
    )

    assert output_cap > bank["retain_chunk_size_chars"]
    assert output_cap >= max(replica["output_tokens_p99"] for replica in replicas)
    assert request_timeout > max(replica["llm_latency_p99_seconds"] for replica in replicas)
    assert wall_timeout >= 3 * request_timeout + 2 * 120

    key_file = REPO_ROOT / "roles/llm_router/defaults/main/56-static-virtual-keys.yml"
    keys = yaml.safe_load(key_file.read_text())["_llm_router_static_virtual_keys"]
    hindsight_key = next(key for key in keys if key["alias"] == "hindsight")
    observed_peak = sum(replica["peak_model_calls_per_minute"] for replica in replicas)
    assert hindsight_key["rpm_limit"] >= 20
    assert hindsight_key["rpm_limit"] >= observed_peak * 2


def test_base_scope_is_bounded_like_retain() -> None:
    rendered = render()
    assert '"1"' in env_line(rendered, "HINDSIGHT_API_LLM_MAX_CONCURRENT")
    assert '"1"' in env_line(rendered, "HINDSIGHT_API_LLM_MAX_RETRIES")
    assert '"1"' in env_line(rendered, "HINDSIGHT_API_CONSOLIDATION_LLM_PARALLELISM")


def test_no_master_key_variable_reaches_the_compose_render() -> None:
    # The template only ever interpolates hindsight_docker_llm_api_key.
    assert "ai_orchestration_model_api_key" not in TEMPLATE_PATH.read_text()
    rendered = render({"hindsight_docker_llm_api_key": "sk-hindsight-test"})
    assert "sk-hindsight-test" in env_line(rendered, "HINDSIGHT_API_LLM_API_KEY")


def test_defaults_carry_no_master_key_fallback() -> None:
    # hindsight_docker_llm_api_key must resolve only to the dedicated
    # per-caller key (or the HINDSIGHT_LLM_API_KEY env override) and fail
    # loudly via `mandatory()` when neither is set -- never fall through to
    # the shared router credential.
    defaults = (REPO_ROOT / "roles/hindsight_docker/defaults/main.yml").read_text()
    assert "ai_orchestration_model_api_key" not in defaults
    assert "hindsight_docker_llm_api_key" in defaults
    assert "mandatory(" in defaults


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
