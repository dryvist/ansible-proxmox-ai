"""Shared constants and helpers for the /hindsight bank ACL tests."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from cel_subset import CelError, authorize, evaluate
from test_authz_render import (
    INVENTORY_VARS,
    REPO_ROOT,
    ROLE_DEFAULTS,
    _render,
    _render_map,
    _routes,
)

HINDSIGHT_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles/agentgateway_docker/defaults/main/20-hindsight-acl.yml").read_text()
)
AGENTS = {"hermes-brain": {}, "hermes-public": {}, "paperclip": {}, "no-tier-agent": {}}
KEYS = {
    "hermes_brain_agentgateway_key": "example-key-trusted",
    "hermes_public_agentgateway_key": "example-key-trusted-two",
    "paperclip_agentgateway_key": "example-key-untrusted",
    "no_tier_agent_agentgateway_key": "example-key-no-tier",
}
TENANT_KEY = "example-hindsight-tenant-key"
SHARED = HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_banks"]["shared"]
SENSITIVE = HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_banks"]["trusted_write"]
API = "/hindsight/v1/default/banks"


def _hindsight_context(tenant_key: str = TENANT_KEY, agent_tiers: dict[str, str] | None = None) -> dict[str, Any]:
    key_fields = _render_map(INVENTORY_VARS["ai_agent_gateway_key_fields"], ai_agents=AGENTS)
    roster_keys = _render_map(
        ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
        ai_agent_gateway_key_fields=key_fields,
        openbao_secrets_by_domain={"agentgateway-agent-keys": KEYS},
    )
    context: dict[str, Any] = {
        name: value
        for name, value in HINDSIGHT_DEFAULTS.items()
        if name
        not in (
            "agentgateway_docker_hindsight_host",
            "agentgateway_docker_hindsight_api_key",
            "agentgateway_docker_hindsight_roster_tiers",
        )
    }
    context["agentgateway_docker_hindsight_host"] = "hindsight.example.com"
    context["agentgateway_docker_hindsight_api_key"] = tenant_key
    context["agentgateway_docker_hindsight_agent_tiers"] = agent_tiers or {"paperclip": "untrusted"}
    context["agentgateway_docker_hindsight_roster_tiers"] = _render_map(
        HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_roster_tiers"],
        agentgateway_docker_roster_keys=roster_keys,
        agentgateway_docker_hindsight_tier_prefixes=context["agentgateway_docker_hindsight_tier_prefixes"],
        agentgateway_docker_hindsight_agent_tiers=context["agentgateway_docker_hindsight_agent_tiers"],
    )
    return context


def _render_hindsight(**kwargs: Any) -> str:
    return _render(AGENTS, {}, KEYS, extra=_hindsight_context(**kwargs))


def _request(
    tier: str | None, method: str, path: str, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "request": {"method": method, "path": path, "headers": {k.lower(): v for k, v in (headers or {}).items()}}
    }
    if tier is not None:
        env["apiKey"] = {"tier": tier}
    return env


def _allowed(rules: list[dict[str, str]], tier: str | None, method: str, path: str, **headers: str) -> bool:
    return authorize(rules, _request(tier, method, path, {k.replace("_", "-"): v for k, v in headers.items()}))[0]


# --- rendered shape ---------------------------------------------------------
