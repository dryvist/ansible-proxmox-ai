"""Private Dify agent apps arrive from env-doc JSON keys and extend the public values."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jinja2
import yaml
from ansible.plugins.filter.core import FilterModule

ROOT = Path(__file__).resolve().parents[2]
GROUP = yaml.safe_load((ROOT / "inventory/group_vars/dify_group.yml").read_text())
ENV = jinja2.Environment(undefined=jinja2.ChainableUndefined)
ENV.filters.update(FilterModule().filters())
PUBLIC_MCP = [{"name": "vikunja"}]


def _resolve(name: str, env: dict[str, str], secrets: dict[str, str] | None = None) -> Any:
    rendered = ENV.from_string(GROUP[name]).render(
        lookup=lambda kind, key: env.get(key, ""),
        bao_local_llm_secrets=secrets or {},
        dify_public_agent_tool_providers=GROUP["dify_public_agent_tool_providers"],
        dify_public_agent_mcp_providers=PUBLIC_MCP,
        dify_public_publish_agent_mcp_apps=GROUP["dify_public_publish_agent_mcp_apps"],
    )
    return yaml.safe_load(rendered)


def test_unset_keys_keep_the_public_values() -> None:
    assert _resolve("dify_docker_private_agent_dsls", {}) == []
    assert _resolve("dify_docker_private_agent_dsl_token", {}) is None
    assert _resolve("dify_docker_agent_tool_providers", {}) == GROUP["dify_public_agent_tool_providers"]
    assert _resolve("dify_docker_agent_mcp_providers", {}) == PUBLIC_MCP
    assert _resolve("dify_docker_publish_agent_mcp_apps", {}) == ["vikunja-clerk"]


def test_supplied_keys_extend_the_public_values() -> None:
    dsl = {"name": "example-app", "url": "https://example.com/a.yml", "sha256": "0" * 64}
    env = {
        "DIFY_PRIVATE_AGENT_DSLS": json.dumps([dsl]),
        "DIFY_PRIVATE_AGENT_TOOL_PROVIDERS": json.dumps({"example-app": {"EXAMPLE_PROVIDER_ID": "example"}}),
        "DIFY_PRIVATE_AGENT_MCP_PROVIDERS": json.dumps([{"name": "example"}]),
        "DIFY_PRIVATE_PUBLISH_AGENT_MCP_APPS": json.dumps(["example-app"]),
    }
    assert _resolve("dify_docker_private_agent_dsls", env) == [dsl]
    providers = _resolve("dify_docker_agent_tool_providers", env)
    assert providers["example-app"] == {"EXAMPLE_PROVIDER_ID": "example"}
    assert providers["vikunja-clerk"] == GROUP["dify_public_agent_tool_providers"]["vikunja-clerk"]
    assert _resolve("dify_docker_agent_mcp_providers", env) == [*PUBLIC_MCP, {"name": "example"}]
    assert _resolve("dify_docker_publish_agent_mcp_apps", env) == ["vikunja-clerk", "example-app"]


def test_private_dsl_token_is_bao_first() -> None:
    env = {"DIFY_PRIVATE_AGENT_DSL_TOKEN": "env-value"}
    bao = {"DIFY_PRIVATE_AGENT_DSL_TOKEN": "bao-value"}
    assert _resolve("dify_docker_private_agent_dsl_token", env, bao) == "bao-value"
    assert _resolve("dify_docker_private_agent_dsl_token", env) == "env-value"
