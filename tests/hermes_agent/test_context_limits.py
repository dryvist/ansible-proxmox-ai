from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader

from _role_files import role_defaults, role_tasks
from test_profile_contract import _RENDER_CONTEXT, _group_vars_all


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
TEMPLATES = (
    "config.yaml.j2",
    "config-profile.yaml.j2",
    "config-public-gateway.yaml.j2",
)


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _render(template: str, limits: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    env = Environment(
        autoescape=False,
        loader=FileSystemLoader(ROLE_ROOT / "templates"),
        trim_blocks=True,
    )
    env.filters["to_json"] = __import__("json").dumps
    env.filters["bool"] = bool
    env.filters["comment"] = lambda value: f"# {value}"
    defaults = role_defaults(ROLE_ROOT)
    context = {**_group_vars_all(), **defaults, **_RENDER_CONTEXT}
    context.update({
        "hermes_agent_context_limits": limits,
        "hermes_agent_profile": next(
            profile for profile in defaults["hermes_agent_profiles"]
            if profile["name"] == "orchestrator"
        ),
        "hermes_agent_api_server_key": "",
        "hermes_agent_slack_bot_token": "",
        "hermes_agent_slack_app_token": "",
        "hermes_agent_a2a_peers": [],
        "hermes_agent_webhook_enabled": False,
        "hermes_agent_webhook_secret": "",
        "hermes_agent_github_route_private_enabled": False,
        "hermes_agent_codex_mcp_enabled": False,
        "hermes_agent_firecrawl_enabled": False,
        "hermes_agent_dashboard_public_url": "https://dashboard.example.invalid",
        "hermes_agent_dashboard_oidc_issuer": "https://auth.example.invalid",
        "hermes_agent_dashboard_oidc_client_id": "fixture-client",
        "hermes_agent_kanban_max_in_progress": 8,
        "hermes_agent_kanban_max_in_progress_per_profile": 8,
    })
    rendered = env.get_template(template).render(**context)
    parsed = yaml.load(rendered, Loader=UniqueKeyLoader)
    return rendered, parsed


def _sample_limits() -> dict[str, Any]:
    return {
        "context_file_max_chars": 12000,
        "file_read_max_chars": 11000,
        "tool_output": {
            "max_bytes": 16000,
            "max_lines": 120,
            "max_line_length": 1200,
        },
        "web": {"extract_char_limit": 9000},
        "delegation": {"max_summary_chars": 3000},
        "tools": {
            "tool_search": {
                "listing_max_tokens": 700,
                "defer": ["kanban_create", "cronjob_list", "memory_recall"],
            },
        },
        "memory": {"memory_char_limit": 2200, "user_char_limit": 1800},
        "compression": {"proactive_prune_tokens": 18000},
        "soul_max_tokens": 400,
        "soul_base_max_tokens": 800,
    }


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(keyset for child in value.values() for keyset in [_walk_keys(child)]))
    if isinstance(value, list):
        return set().union(*(_walk_keys(child) for child in value))
    return set()


def test_bundle_limits_render_once_at_upstream_paths_in_all_configs() -> None:
    limits = _sample_limits()
    for template in TEMPLATES:
        rendered, config = _render(template, limits)

        assert config["context_file_max_chars"] == 12000
        assert config["file_read_max_chars"] == 11000
        assert config["tool_output"] == {
            "max_bytes": 16000,
            "max_lines": 120,
            "max_line_length": 1200,
        }
        assert config["web"]["extract_char_limit"] == 9000
        assert config["delegation"]["max_summary_chars"] == 3000
        assert config["tools"]["tool_search"] == {
            "listing_max_tokens": 700,
            "defer": ["kanban_create", "cronjob_list", "memory_recall"],
        }
        assert config["memory"]["memory_char_limit"] == 2200
        assert config["memory"]["user_char_limit"] == 1800
        assert config["compression"]["proactive_prune_tokens"] == 18000
        assert "soul_max_tokens" not in rendered
        assert "soul_base_max_tokens" not in rendered
        for key in ("context_file_max_chars", "file_read_max_chars", "max_bytes", "memory_char_limit"):
            assert rendered.count(f"{key}:") == 1


def test_empty_bundle_limits_render_no_optional_caps() -> None:
    optional_keys = {
        "context_file_max_chars", "file_read_max_chars", "max_bytes", "max_lines",
        "max_line_length", "extract_char_limit", "max_summary_chars", "listing_max_tokens",
        "defer", "memory_char_limit", "user_char_limit", "proactive_prune_tokens",
    }
    for template in TEMPLATES:
        rendered, config = _render(template, {})
        assert not (optional_keys & _walk_keys(config)), template
        assert "tool_output:" not in rendered
        assert "tools:" not in rendered
        assert "delegation:" not in rendered


def test_context_limits_are_loaded_from_optional_controller_bundle_file() -> None:
    tasks = role_tasks(ROLE_ROOT)
    stat = next(task for task in tasks if task["name"] == "Stat optional bundle context limits on the controller")
    assert stat["ansible.builtin.stat"]["path"] == "{{ hermes_agent_bundle_path }}/limits.json"
    assert stat["delegate_to"] == "localhost"

    load = next(task for task in tasks if task["name"] == "Load bundle context limits and apply the role override")
    expression = load["ansible.builtin.set_fact"]["hermes_agent_context_limits"]
    assert "from_json" in expression
    assert "combine(hermes_agent_context_limits_override, recursive=true)" in expression
    assert role_defaults(ROLE_ROOT)["hermes_agent_context_limits_override"] == {}
