"""Shared docker-compose.yml.j2 render helpers for the hindsight_docker test
suite — same bare-module convention as tests/_runner_stubs.py (no
tests/__init__.py exists, so pytest puts each collected directory on
sys.path; a leading-underscore, non test_*.py module is never collected as
tests itself, only imported by the ones that need it).
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
    "hindsight_docker_db_url": (
        "postgresql://hindsight:test-db-password@postgres-ai-1.example.test:5432/hindsight"
    ),
    "hindsight_docker_vector_extension": "pgvector",
    "hindsight_docker_llm_base_url": "https://llm.example.test/v1",
    "hindsight_docker_llm_model": "fixture-primary-model",
    "hindsight_docker_llm_api_key": "sk-hindsight-test",
    "hindsight_docker_retain_llm_model": "cheap",
    "hindsight_docker_retain_llm_max_concurrent": 1,
    "hindsight_docker_retain_llm_max_retries": 1,
    "hindsight_docker_run_migrations_on_startup": False,
    "hindsight_docker_retain_wall_timeout": 720,
    "hindsight_docker_consolidation_wall_timeout": 2400,
    "hindsight_docker_reflect_wall_timeout": 120,
    "hindsight_docker_refresh_mental_model_wall_timeout": 120,
    "hindsight_docker_embeddings_provider": "local",
    "hindsight_docker_mcp_stateless": True,
    "hindsight_docker_worker_id": "hindsight-1",
    "hindsight_docker_cp_access_key": "test-cp-key",
}


def render(context: dict | None = None) -> str:
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False)
    env.filters["string"] = str
    env.filters["lower"] = str.lower
    template = env.from_string(TEMPLATE_PATH.read_text())
    return template.render(**{**DEFAULT_CONTEXT, **(context or {})})


def env_line(rendered: str, key: str) -> str:
    for line in rendered.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            return stripped
    raise AssertionError(f"{key} not found in rendered compose:\n{rendered}")
