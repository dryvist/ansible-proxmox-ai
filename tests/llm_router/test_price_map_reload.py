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
SCHEDULE_FIELDS = {"interval_hours", "last_run"}
SOURCE_FIELDS = {
    "source",
    "url",
    "fallback_reason",
    "loaded_at",
    "source_revision",
    "etag",
    "model_count",
}
REGISTERED_FIELDS = {
    "llm_router_model_cost_map_schedule_status": SCHEDULE_FIELDS,
    "llm_router_model_cost_map_source_status": SOURCE_FIELDS,
}


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
        "llm_router_trace_environment": "test",
        "llm_router_log_level": "INFO",
        "llm_router_openrouter_models": [],
        "llm_router_opencode_models": [],
        "llm_router_hermes_cloud_models_credentialed": [],
        "llm_router_resolved_credentials": {},
        "llm_router_price_map_url": price_map_url,
    }
    return env.get_template("litellm.env.j2").render(**values)


def _debug_task_is_safe(task_text: str) -> bool:
    if "llm_router_master_key" in task_text or re.search(r"\bheaders\b", task_text, re.IGNORECASE):
        return False
    for variable, allowed_fields in REGISTERED_FIELDS.items():
        field_pattern = "|".join(re.escape(field) for field in allowed_fields)
        field_reference = re.compile(rf"{re.escape(variable)}\.json\.(?:{field_pattern})\b")
        for occurrence in re.finditer(re.escape(variable), task_text):
            if field_reference.match(task_text, occurrence.start()) is None:
                return False
    return True


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


def test_schedule_and_source_status_are_reported_without_secrets():
    schedule_tasks = yaml.safe_load((ROLE_ROOT / "tasks/price-map-reload.yml").read_text(encoding="utf-8"))
    router_tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    schedule_uri = next(
        task
        for task in schedule_tasks
        if task.get("ansible.builtin.uri", {}).get("url", "").endswith(
            "/schedule/model_cost_map_reload/status"
        )
    )
    schedule_debug = next(
        task for task in schedule_tasks if task.get("name") == "Report the model cost map reload schedule status"
    )
    source_uri = next(
        task
        for task in router_tasks
        if task.get("ansible.builtin.uri", {}).get("url", "").endswith("/model/cost_map/source")
    )
    source_debug = next(
        task for task in router_tasks if task.get("name") == "Report this router's model cost map source status"
    )

    assert schedule_uri["no_log"] is True
    assert source_uri["no_log"] is True
    assert source_uri["ansible.builtin.uri"]["status_code"] == 200
    assert source_uri["ansible.builtin.uri"]["timeout"] == "{{ llm_router_seed_request_timeout }}"
    assert source_uri["changed_when"] is False
    assert source_uri["failed_when"] is False
    assert source_uri["check_mode"] is False
    assert set(schedule_debug["ansible.builtin.debug"]["msg"]) == SCHEDULE_FIELDS
    assert set(source_debug["ansible.builtin.debug"]["msg"]) == SOURCE_FIELDS
    for task in (schedule_debug, source_debug):
        assert _debug_task_is_safe(yaml.safe_dump(task))
        for field, value in task["ansible.builtin.debug"]["msg"].items():
            assert f".json.{field}" in value
            assert "| default('absent')" in value


def test_debug_safety_check_rejects_unscoped_registered_values_and_secrets():
    assert not _debug_task_is_safe("{{ llm_router_model_cost_map_schedule_status }}")
    assert not _debug_task_is_safe("{{ llm_router_model_cost_map_schedule_status.json.unexpected }}")
    assert not _debug_task_is_safe("{{ llm_router_master_key }}")
    assert not _debug_task_is_safe("headers: {{ llm_router_model_cost_map_schedule_status.json.last_run }}")


def test_source_status_read_is_per_router_and_outside_run_once_blocks():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/main.yml").read_text(encoding="utf-8"))
    source_read_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.uri", {}).get("url", "").endswith("/model/cost_map/source")
    )
    source_read = tasks[source_read_index]
    assert source_read.get("run_once") is not True
    assert not any("block" in task and task.get("run_once") for task in tasks[:source_read_index + 1])
    assert source_read_index > next(
        index for index, task in enumerate(tasks) if task.get("name") == "Verify this member serves the rendered model list"
    )


def test_schedule_task_is_imported_after_database_consumer_seeds():
    tasks = yaml.safe_load((ROLE_ROOT / "tasks/seed-consumers.yml").read_text(encoding="utf-8"))
    names = [task.get("name") for task in tasks]
    assert names.index("Schedule the LiteLLM model cost map reload") > names.index(
        "Seed the caller-facing roles the first time this proxy carries them"
    )
    assert tasks[names.index("Schedule the LiteLLM model cost map reload")][
        "ansible.builtin.import_tasks"
    ] == "price-map-reload.yml"
