"""Loaders shared by the evaluation-instance tests.

Expressions render through Ansible's own Templar, so lookup('env'), undef(), mandatory and the
roles' filters behave as they do in a converge. Resolution is lazy, as in Ansible: a variable is
templated only when an expression reads it. Nothing here reads the controller environment except
through the test's own monkeypatched values.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

ROOT = Path(__file__).resolve().parents[2]
ROLES = ROOT / "roles"
GROUP_VARS = ROOT / "inventory/group_vars"

PRODUCTION = "production-marker"
EVALUATION = "evaluation-marker"
GUEST = "guest-marker"

DIFY_EVAL_PATH = "ai/eval/specialist-platform/dify"
LANGFLOW_EVAL_PATH = "ai/eval/specialist-platform/langflow"

# What the Phase 0 prefetch publishes into bao_* facts, with production markers. An evaluation
# host is never a prefetch target, so its real context has the empty defaults; tests inject these
# maps to prove the evaluation overrides do not read them even when they are present.
PRODUCTION_PREFETCH = {
    "bao_local_llm_secrets": {
        "DIFY_SECRET_KEY": PRODUCTION,
        "DIFY_DB_PASSWORD": PRODUCTION,
        "DIFY_REDIS_PASSWORD": PRODUCTION,
        "DIFY_SANDBOX_API_KEY": PRODUCTION,
        "DIFY_WEAVIATE_API_KEY": PRODUCTION,
        "DIFY_PLUGIN_DAEMON_KEY": PRODUCTION,
        "DIFY_INNER_API_KEY": PRODUCTION,
        "DIFY_ADMIN_EMAIL": PRODUCTION,
        "DIFY_ADMIN_PASSWORD": PRODUCTION,
        "LANGFLOW_SUPERUSER": PRODUCTION,
        "LANGFLOW_SUPERUSER_PASSWORD": PRODUCTION,
        "LANGFLOW_DB_PASSWORD": PRODUCTION,
        "LANGFLOW_SECRET_KEY": PRODUCTION,
    },
    "bao_apps_secrets": {"dify_llm_router_key": PRODUCTION},
}


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def role_defaults(role: str) -> dict:
    return load_yaml(ROLES / role / "defaults/main.yml")


def group_vars(group: str) -> dict:
    return load_yaml(GROUP_VARS / f"{group}.yml") or {}


def core_defaults() -> dict:
    """What group_vars/all gives every host: the empty bao_* maps, and the production converge writer."""
    core = load_yaml(GROUP_VARS / "all/00-core.yml")
    seed = load_yaml(GROUP_VARS / "all/openbao_secrets/00-seed-vars.yml")
    values = {key: core[key] for key in ("bao_local_llm_secrets", "bao_apps_secrets")}
    values["openbao_secrets_converge_writer_token"] = seed["openbao_secrets_converge_writer_token"]
    return values


def role_tasks(role: str, file: str) -> list[dict]:
    return load_yaml(ROLES / role / "tasks" / file)


def task_named(role: str, file: str, name: str) -> dict:
    return next(task for task in role_tasks(role, file) if task.get("name") == name)


def host_context(role: str, group: str | None = None, prefetch: dict | None = None) -> dict:
    """Variables an evaluation host sees: role defaults, the empty core defaults, then group_vars.

    `prefetch` simulates production maps reaching the host. Pass None for the real context.
    """
    context = dict(role_defaults(role))
    context.update(core_defaults())
    if prefetch:
        context.update(prefetch)
    if group:
        context.update(group_vars(group))
    return context


def guest_key_facts() -> dict:
    """The on-guest key file both roles stat and slurp before choosing a caller value."""
    content = base64.b64encode(f"{GUEST}\n".encode()).decode()
    return {
        "dify_docker_secret_key_stat": {"stat": {"exists": True}},
        "dify_docker_secret_key_file": {"content": content},
        "langflow_docker_secret_key_stat": {"stat": {"exists": True}},
        "langflow_docker_secret_key_file": {"content": content},
    }


def render(expression: Any, variables: dict) -> Any:
    templar = Templar(loader=DataLoader())
    templar.available_variables = {
        key: trust_as_template(value) if isinstance(value, str) else value
        for key, value in variables.items()
    }
    if isinstance(expression, str):
        return templar.template(trust_as_template(expression))
    return expression
