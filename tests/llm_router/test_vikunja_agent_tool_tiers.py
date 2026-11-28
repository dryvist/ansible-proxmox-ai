"""Per-bot tool tiers for the tracker's built-in tool endpoint."""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "hermes_agent"))
from _shell_harness_shared import defaults_of, render  # noqa: E402

ROUTER = defaults_of("llm_router")
BOT_ROLES = [
    "workstation", "hermes", "pepper", "untrusted", "jobs", "inbox",
    "triage", "nightly", "windows", "acceptance",
]
CREATE_ONLY = {"untrusted", "inbox"}


def _tiers() -> list[dict]:
    value = ROUTER["llm_router_vikunja_clerk_app_tiers"]
    if isinstance(value, str):
        value = yaml.safe_load(render(value, dict(ROUTER)))
    return value


def test_switch_defaults_off() -> None:
    assert ROUTER["llm_router_hermes_vikunja_clerk_variants_enabled"] is False
    assert ROUTER["llm_router_vikunja_clerk_bot_roles"] == BOT_ROLES


def test_one_native_tier_per_bot_role() -> None:
    by_id = {row["id"]: row for row in _tiers()}
    assert set(by_id) == {f"vikunja-{role}" for role in BOT_ROLES}
    for role in BOT_ROLES:
        row = by_id[f"vikunja-{role}"]
        assert row["app"] == "vikunja-native"
        assert row["tier"] == "write"
        assert row["token_field"] == f"VIKUNJA_AGENT_{role.upper()}_TOKEN"


def test_tool_lists_hold_no_delete_and_create_only_roles_only_create() -> None:
    names = ROUTER["llm_router_vikunja_agent_tool_names"]
    assert "tasks_create" in names and "do_action" in names
    assert not [n for n in names if n.endswith("_delete")]
    assert not {"projects_create", "projects_update", "labels_create", "labels_update"} & set(names)
    for row in _tiers():
        role = row["id"].removeprefix("vikunja-")
        tools = row["allowed_tools"]
        if isinstance(tools, str):
            tools = yaml.safe_load(render(tools, dict(ROUTER)))
        assert tools == (["tasks_create"] if role in CREATE_ONLY else names)


def test_native_app_uses_http_and_the_builtin_path() -> None:
    app = ROUTER["llm_router_app_agent_apps"]["vikunja-native"]
    assert "command" not in app
    text = (REPO_ROOT / "roles/llm_router/defaults/main/81-app-agents.yml").read_text(encoding="utf-8")
    assert "/api/v2/mcp" in text
    assert app["url_field"] == "VIKUNJA_MCP_URL"
    legacy = ROUTER["llm_router_app_agent_apps"]["vikunja"]
    assert legacy["command"] == "/usr/bin/npx"
    assert [r["id"] for r in ROUTER["llm_router_app_agent_tiers"] if r["app"] == "vikunja"] == [
        "vikunja-read", "vikunja-write", "vikunja-clerk",
    ]


def _rendered_matrix(enabled: bool) -> tuple[list[dict], dict]:
    context = dict(ROUTER)
    context.update(
        {
            "llm_router_hermes_vikunja_clerk_variants_enabled": enabled,
            "llm_router_vikunja_clerk_app_tiers": _tiers(),
            "hermes_agent_profiles": [],
            "ai_agents": {
                "vikunja-workstation": {"trust_tier": "trusted"},
            },
            "openbao_secrets_by_domain": {
                "app-agent-vikunja-workstation": {
                    "VIKUNJA_AGENT_WORKSTATION_TOKEN": "bot-token-placeholder",
                },
            },
            "bao_local_llm_secrets": {"VIKUNJA_MCP_URL": "https://tracker.invalid/"},
        }
    )
    matrix_text = render(context["_llm_router_app_agent_matrix"], context)
    matrix = yaml.safe_load(matrix_text)
    context["_llm_router_app_agent_matrix"] = matrix
    return matrix, context


def _template_environment() -> jinja2.Environment:
    env = jinja2.Environment(trim_blocks=True, undefined=jinja2.StrictUndefined)
    env.filters.update(
        {
            "bool": lambda value: str(value).lower() in {"1", "true", "yes", "on"},
            "comment": lambda value: "\n".join(f"# {line}" for line in str(value).splitlines()),
            "regex_replace": lambda value, pattern, replacement: re.sub(pattern, replacement, str(value)),
            "to_json": json.dumps,
            "b64encode": lambda value: base64.b64encode(str(value).encode()).decode(),
            "urlencode": lambda value: value,
        }
    )
    return env


def test_templates_render_one_gated_native_server() -> None:
    template_dir = REPO_ROOT / "roles/llm_router/templates"
    env = _template_environment()
    server_template = (template_dir / "mcp-servers.yaml.j2").read_text(encoding="utf-8")
    env_template = (template_dir / "litellm.env.j2").read_text(encoding="utf-8")

    matrix, context = _rendered_matrix(True)
    row = next(row for row in matrix if row["id"] == "vikunja-workstation")
    context.update(
        {
            "llm_router_mcp_zammad_server_id": "zammad",
            "llm_router_mcp_zammad_command": "/usr/bin/zammad-mcp",
            "llm_router_mcp_zammad_args": [],
            "llm_router_mcp_zammad_allowed_tools": [],
            "llm_router_mcp_slack_enabled": False,
            "llm_router_mcp_next_ai_drawio_enabled": False,
            "ansible_managed": "managed by Ansible",
        }
    )
    servers = yaml.safe_load(env.from_string(server_template).render(**context))["mcp_servers"]
    server = servers["vikunja_workstation"]
    assert server["url"] == f"os.environ/{row['url_env_key']}"
    assert server["auth_type"] == "bearer"
    assert server["auth_value"] == f"os.environ/{row['token_env_key']}"

    env_context = {
        **context,
        "groups": {},
        "llm_router_mcp_zammad_url": "",
        "llm_router_mcp_zammad_http_token": "",
        "llm_router_master_key": "",
        "llm_router_llm_large_bearer": "",
        "llm_router_hermes_identity_bearers": {},
        "llm_router_llm_large_standby_base_url": "",
        "llm_router_retry_max_delay_seconds": 0,
        "llm_router_chatgpt_token_dir": "",
        "llm_router_openrouter_models": [],
        "llm_router_opencode_models": [],
        "_llm_router_m_hermes_cloud_models_credentialed": [],
        "_llm_router_m_resolved_credentials": {},
        "llm_router_redis_host": "",
        "llm_router_db_host": "",
        "llm_router_oidc_enabled": False,
        "llm_router_untrusted_classify_enabled": False,
        "llm_router_lane_names": [],
        "llm_router_hermes_job_tag_map": {},
        "ai_orchestration_otel_endpoint": "",
        "llm_router_trace_environment": "",
        "llm_router_log_level": "INFO",
        "llm_router_llm_large_standby_base_url": "",
    }
    rendered_env = env.from_string(env_template).render(**env_context)
    env_values = dict(
        line.split("=", 1)
        for line in rendered_env.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    assert env_values[row["url_env_key"]] == "https://tracker.invalid/api/v2/mcp"
    assert env_values[row["token_env_key"]] == "bot-token-placeholder"

    _, disabled_context = _rendered_matrix(False)
    disabled_servers = yaml.safe_load(
        env.from_string(server_template).render(**{**context, **disabled_context})
    )["mcp_servers"]
    assert not any(name.startswith("vikunja_") for name in disabled_servers)
