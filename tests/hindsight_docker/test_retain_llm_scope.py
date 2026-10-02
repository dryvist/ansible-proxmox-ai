"""Hindsight's background retain (fact extraction) scope must render onto the
fabric's light `cheap` role, at concurrency 1, with a bounded retry budget —
not the fabric-wide primary/master-key defaults every other LLM call here
uses.
"""

from __future__ import annotations

from pathlib import Path

from _compose_render import env_line, render

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hindsight_docker/templates/docker-compose.yml.j2"


def test_retain_scope_is_a_light_model_not_the_shared_primary() -> None:
    rendered = render()
    assert '"cheap"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MODEL")
    # The global scope (reflect/consolidation) is untouched -- still the
    # accurate primary tier -- proving this is an ADDITIONAL override, not a
    # blanket downgrade of every Hindsight LLM call.
    assert "fixture-primary-model" in env_line(rendered, "HINDSIGHT_API_LLM_MODEL")


def test_retain_scope_admits_one_in_flight_extraction() -> None:
    rendered = render()
    assert '"1"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT")


def test_retain_scope_does_not_retry_into_a_busy_local_slot() -> None:
    rendered = render()
    assert '"1"' in env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES")


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
