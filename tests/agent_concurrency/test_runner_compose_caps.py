"""Agent worker caps render from the one shared concurrency setting."""

from pathlib import Path

import jinja2
import yaml
from jinja2 import meta


REPO_ROOT = Path(__file__).resolve().parents[2]
ALL_VARS = yaml.safe_load((REPO_ROOT / "inventory/group_vars/all.yml").read_text())
AGENT_CAP = int(ALL_VARS["ai_agent_default_concurrency"])


def _render_compose(role: str) -> dict:
    template_path = REPO_ROOT / "roles" / role / "templates/docker-compose.yml.j2"
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    source = template_path.read_text()
    names = meta.find_undeclared_variables(env.parse(source))
    context = {name: "fixture" for name in names}
    context["ai_agent_default_concurrency"] = AGENT_CAP
    if role == "langflow_docker":
        context.update(
            langflow_docker_db_service="postgres",
            langflow_docker_redis_service="redis",
            langflow_docker_app_service="langflow",
            langflow_docker_redis_port=6379,
        )
    return yaml.safe_load(env.from_string(source).render(**context))


def test_dify_worker_pool_uses_the_shared_fixed_cap() -> None:
    compose = _render_compose("dify_docker")
    worker_env = compose["services"]["worker"]["environment"]

    assert AGENT_CAP == 8
    assert worker_env["CELERY_WORKER_AMOUNT"] == str(AGENT_CAP)
    assert worker_env["CELERY_AUTO_SCALE"] == "false"
    assert worker_env["GRAPH_ENGINE_MAX_WORKERS"] == str(AGENT_CAP)


def test_langflow_worker_pool_uses_the_shared_cap_with_redis_queue() -> None:
    compose = _render_compose("langflow_docker")
    app = compose["services"]["langflow"]

    assert app["environment"]["LANGFLOW_WORKERS"] == str(AGENT_CAP)
    assert app["environment"]["LANGFLOW_JOB_QUEUE_TYPE"] == "redis"
    assert app["environment"]["LANGFLOW_REDIS_QUEUE_URL"] == "redis://redis:6379/1"
    assert app["depends_on"]["redis"]["condition"] == "service_healthy"
    assert "healthcheck" in compose["services"]["redis"]
