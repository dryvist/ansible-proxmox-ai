"""Shared docker-compose.yml.j2 render helpers for the hindsight_docker test
suite — same bare-module convention as tests/_runner_stubs.py (no
tests/__init__.py exists, so pytest puts each collected directory on
sys.path; a leading-underscore, non test_*.py module is never collected as
tests itself, only imported by the ones that need it).
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "roles/hindsight_docker/templates/docker-compose.yml.j2"
ROLE_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles/hindsight_docker/defaults/main.yml").read_text()
)
INVENTORY_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "inventory/group_vars/all.yml").read_text()
)

DEFAULT_CONTEXT = {
    "hindsight_docker_image": ROLE_DEFAULTS["hindsight_docker_image"],
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
    "hindsight_docker_llm_model": INVENTORY_DEFAULTS["hindsight_retain_model"],
    "hindsight_docker_llm_api_key": "sk-hindsight-test",
    "hindsight_docker_retain_llm_model": INVENTORY_DEFAULTS["hindsight_retain_model"],
    "hindsight_docker_retain_llm_max_concurrent": ROLE_DEFAULTS[
        "hindsight_docker_retain_llm_max_concurrent"
    ],
    "hindsight_docker_retain_llm_max_retries": ROLE_DEFAULTS[
        "hindsight_docker_retain_llm_max_retries"
    ],
    "hindsight_docker_retain_llm_initial_backoff": ROLE_DEFAULTS[
        "hindsight_docker_retain_llm_initial_backoff"
    ],
    "hindsight_docker_retain_llm_max_backoff": ROLE_DEFAULTS[
        "hindsight_docker_retain_llm_max_backoff"
    ],
    "hindsight_docker_retain_llm_timeout": ROLE_DEFAULTS[
        "hindsight_docker_retain_llm_timeout"
    ],
    "hindsight_docker_retain_max_completion_tokens": ROLE_DEFAULTS[
        "hindsight_docker_retain_max_completion_tokens"
    ],
    "hindsight_docker_worker_max_retries": ROLE_DEFAULTS[
        "hindsight_docker_worker_max_retries"
    ],
    "hindsight_docker_worker_task_retry_backoff_seconds": ROLE_DEFAULTS[
        "hindsight_docker_worker_task_retry_backoff_seconds"
    ],
    "hindsight_docker_llm_max_concurrent": 1,
    "hindsight_docker_llm_max_retries": 1,
    "hindsight_docker_llm_initial_backoff": ROLE_DEFAULTS[
        "hindsight_docker_retain_llm_initial_backoff"
    ],
    "hindsight_docker_llm_max_backoff": ROLE_DEFAULTS[
        "hindsight_docker_retain_llm_max_backoff"
    ],
    "hindsight_docker_consolidation_llm_parallelism": 1,
    "hindsight_docker_run_migrations_on_startup": False,
    "hindsight_docker_retain_wall_timeout": ROLE_DEFAULTS[
        "hindsight_docker_retain_wall_timeout"
    ],
    "hindsight_docker_consolidation_wall_timeout": ROLE_DEFAULTS[
        "hindsight_docker_consolidation_wall_timeout"
    ],
    "hindsight_docker_reflect_wall_timeout": ROLE_DEFAULTS[
        "hindsight_docker_reflect_wall_timeout"
    ],
    "hindsight_docker_refresh_mental_model_wall_timeout": ROLE_DEFAULTS[
        "hindsight_docker_refresh_mental_model_wall_timeout"
    ],
    "hindsight_docker_embeddings_provider": "local",
    "hindsight_docker_mcp_stateless": True,
    "hindsight_docker_worker_id": "hindsight-1",
    "hindsight_docker_cp_access_key": "test-cp-key",
    "hindsight_docker_api_key": "test-hindsight-api-key",
}


def render(
    context: dict | None = None,
    *,
    include_api_key: bool = True,
    strict: bool = False,
) -> str:
    env = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=False,
        undefined=jinja2.StrictUndefined if strict else jinja2.Undefined,
    )
    env.filters["string"] = str
    env.filters["lower"] = str.lower
    template = env.from_string(TEMPLATE_PATH.read_text())
    render_context = {**DEFAULT_CONTEXT, **(context or {})}
    if not include_api_key:
        render_context.pop("hindsight_docker_api_key", None)
    return template.render(**render_context)


def env_line(rendered: str, key: str) -> str:
    for line in rendered.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            return stripped
    raise AssertionError(f"{key} not found in rendered compose:\n{rendered}")
