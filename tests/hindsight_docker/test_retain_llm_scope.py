"""Hindsight's background retain (fact extraction) scope must render onto the
fabric's light `cheap` role, at concurrency 1, with a bounded retry budget —
not the fabric-wide primary/master-key defaults every other LLM call here
uses.
"""

from __future__ import annotations

from pathlib import Path

import jinja2

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hindsight_docker/templates/docker-compose.yml.j2"

DEFAULT_CONTEXT = {
    "hindsight_docker_image": "ghcr.io/vectorize-io/hindsight:0.9.0",
    "hindsight_docker_container_name": "hindsight",
    "hindsight_docker_api_port": 8888,
    "hindsight_docker_cp_port": 9999,
    "hindsight_docker_db_user": "hindsight",
    "hindsight_docker_db_password": "test-db-password",
    "hindsight_docker_db_host": "postgres-ai-1.example.test",
    "hindsight_docker_db_port": 5432,
    "hindsight_docker_db_name": "hindsight",
    "hindsight_docker_llm_base_url": "https://llm.example.test/v1",
    "hindsight_docker_llm_model": "fixture-primary-model",
    "hindsight_docker_llm_api_key": "sk-hindsight-test",
    "hindsight_docker_retain_llm_model": "cheap",
    "hindsight_docker_retain_llm_max_concurrent": 1,
    "hindsight_docker_retain_llm_max_retries": 1,
    "hindsight_docker_embeddings_provider": "local",
    "hindsight_docker_mcp_stateless": True,
    "hindsight_docker_worker_id": "hindsight-1",
    "hindsight_docker_cp_access_key": "test-cp-key",
}


def _render(context=None) -> str:
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False)
    env.filters["string"] = str
    env.filters["lower"] = str.lower
    template = env.from_string(TEMPLATE_PATH.read_text())
    return template.render(**{**DEFAULT_CONTEXT, **(context or {})})


def _env_line(rendered: str, key: str) -> str:
    for line in rendered.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            return stripped
    raise AssertionError(f"{key} not found in rendered compose:\n{rendered}")


def test_retain_scope_is_a_light_model_not_the_shared_primary() -> None:
    rendered = _render()
    assert '"cheap"' in _env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MODEL")
    # The global scope (reflect/consolidation) is untouched -- still the
    # accurate primary tier -- proving this is an ADDITIONAL override, not a
    # blanket downgrade of every Hindsight LLM call.
    assert "fixture-primary-model" in _env_line(rendered, "HINDSIGHT_API_LLM_MODEL")


def test_retain_scope_admits_one_in_flight_extraction() -> None:
    rendered = _render()
    assert '"1"' in _env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT")


def test_retain_scope_does_not_retry_into_a_busy_local_slot() -> None:
    rendered = _render()
    assert '"1"' in _env_line(rendered, "HINDSIGHT_API_RETAIN_LLM_MAX_RETRIES")


def test_no_master_key_variable_reaches_the_compose_render() -> None:
    # The template only ever interpolates hindsight_docker_llm_api_key.
    assert "ai_orchestration_model_api_key" not in TEMPLATE_PATH.read_text()
    rendered = _render({"hindsight_docker_llm_api_key": "sk-hindsight-test"})
    assert "sk-hindsight-test" in _env_line(rendered, "HINDSIGHT_API_LLM_API_KEY")


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
