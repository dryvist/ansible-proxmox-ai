"""Render and task-order contracts for LiteLLM's model cost map reload."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles/llm_router"
TEMPLATE_ROOT = ROLE_ROOT / "templates"


def _defaults() -> dict:
    return yaml.safe_load((ROLE_ROOT / "defaults/main/60-ops.yml").read_text(encoding="utf-8"))


def _render_env(price_map_url: str) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_ROOT),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters.update(
        {
            "bool": lambda value: str(value).lower() in {"1", "true", "yes", "on"},
            "comment": lambda value: "\n".join(f"# {line}" for line in str(value).splitlines()),
            "regex_replace": lambda value, pattern, replacement: re.sub(pattern, replacement, str(value)),
            "to_json": json.dumps,
            "urlencode": lambda value: quote(str(value), safe=""),
        }
    )
    values = {
        "ansible_managed": "managed by Ansible",
        "llm_router_mcp_zammad_url": "https://zammad.example",
        "llm_router_mcp_zammad_http_token": "placeholder",
        "llm_router_mcp_slack_enabled": False,
        "llm_router_master_key": "sk-placeholder",
        "llm_router_llm_large_bearer": "sk-placeholder",
        "llm_router_hermes_identity_bearers": {},
        "llm_router_llm_large_standby_base_url": "",
        "llm_router_retry_max_delay_seconds": 60,
        "llm_router_chatgpt_token_dir": "/tmp/chatgpt",
        "llm_router_redis_host": "",
        "llm_router_db_host": "",
        "llm_router_oidc_enabled": False,
        "ai_orchestration_otel_endpoint": "http://otel.example/v1/traces",
        "llm_router_langfuse_enabled": False,
        "llm_router_trace_environment": "test",
        "llm_router_log_level": "INFO",
        "llm_router_openrouter_models": [],
        "llm_router_opencode_models": [],
        "llm_router_hermes_cloud_models_credentialed": [],
        "llm_router_resolved_credentials": {},
        "llm_router_price_map_url": price_map_url,
    }
    return env.get_template("litellm.env.j2").render(**values)


def test_default_price_map_url_is_not_rendered():
    defaults = _defaults()
    assert defaults["llm_router_price_map_reload_hours"] == 24
    assert defaults["llm_router_price_map_url"] == ""
    assert "LITELLM_MODEL_COST_MAP_URL=" not in _render_env(defaults["llm_router_price_map_url"])


def test_configured_price_map_url_is_rendered():
    url = "https://prices.example/model_prices.json"
    rendered = _render_env(url)
    assert f"LITELLM_MODEL_COST_MAP_URL={url}" in rendered


def test_schedule_status_is_read_before_schedule_post():
    path = ROLE_ROOT / "tasks/price-map-reload.yml"
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    status_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.uri", {}).get("url", "").endswith(
            "/schedule/model_cost_map_reload/status"
        )
    )
    post_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.uri", {}).get("method") == "POST"
        and "/schedule/model_cost_map_reload?hours="
        in task.get("ansible.builtin.uri", {}).get("url", "").replace("\n", "")
    )
    assert status_index < post_index
    assert tasks[status_index]["no_log"] is True
    assert tasks[post_index]["no_log"] is True


def test_schedule_task_is_imported_after_database_consumer_seeds():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/seed-consumers.yml").read_text(encoding="utf-8"))
    names = [task.get("name") for task in tasks]
    assert names.index("Schedule the LiteLLM model cost map reload") > names.index(
        "Seed the caller-facing roles the first time this proxy carries them"
    )
    assert tasks[names.index("Schedule the LiteLLM model cost map reload")][
        "ansible.builtin.import_tasks"
    ] == "price-map-reload.yml"
