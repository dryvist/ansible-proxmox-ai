"""Shared constants and helpers for the /hindsight bank ACL tests."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from cel_subset import authorize
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
# The roster is the only agent list: names and explicit tiers, as the published
# inventory carries them. Banks are derived from it by the role defaults.
AGENTS = {
    "hermes-brain": {"trust_tier": "trusted"},
    "hermes-ops": {"trust_tier": "trusted"},
    "paperclip": {"trust_tier": "untrusted"},
    "scout-open": {"trust_tier": "untrusted"},
    "no-tier-agent": {},
}
KEYS = {
    "hermes_brain_agentgateway_key": "example-key-trusted",
    "hermes_ops_agentgateway_key": "example-key-trusted-two",
    "paperclip_agentgateway_key": "example-key-untrusted",
    "scout_open_agentgateway_key": "example-key-untrusted-two",
    "no_tier_agent_agentgateway_key": "example-key-no-tier",
}
TENANT_KEY = "example-hindsight-tenant-key"
API = "/hindsight/v1/default/banks"
DERIVED = ("roster_tiers", "agent_banks")


def _hindsight_context(
    tenant_key: str = TENANT_KEY, agents: dict[str, Any] | None = None, keys: dict[str, str] | None = None
) -> dict[str, Any]:
    roster = AGENTS if agents is None else agents
    caller_keys = KEYS if keys is None else keys
    key_fields = _render_map(INVENTORY_VARS["ai_agent_gateway_key_fields"], ai_agents=roster)
    roster_keys = _render_map(
        ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
        ai_agent_gateway_key_fields=key_fields,
        openbao_secrets_by_domain={"agentgateway-agent-keys": caller_keys},
    )
    context: dict[str, Any] = {
        name: value
        for name, value in HINDSIGHT_DEFAULTS.items()
        if name not in {f"agentgateway_docker_hindsight_{key}" for key in DERIVED}
        and name not in ("agentgateway_docker_hindsight_host", "agentgateway_docker_hindsight_api_key")
    }
    context["agentgateway_docker_hindsight_host"] = "hindsight.example.com"
    context["agentgateway_docker_hindsight_api_key"] = tenant_key
    context["agentgateway_docker_hindsight_roster_tiers"] = _render_map(
        HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_roster_tiers"],
        agentgateway_docker_roster_keys=roster_keys,
        ai_agents=roster,
    )
    context["agentgateway_docker_hindsight_agent_banks"] = _render_map(
        HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_agent_banks"],
        agentgateway_docker_hindsight_roster_tiers=context["agentgateway_docker_hindsight_roster_tiers"],
    )
    return context


def _render_hindsight(**kwargs: Any) -> str:
    roster = AGENTS if kwargs.get("agents") is None else kwargs["agents"]
    caller_keys = KEYS if kwargs.get("keys") is None else kwargs["keys"]
    return _render(roster, {}, caller_keys, extra=_hindsight_context(**kwargs))


_CONTEXT = _hindsight_context()
TIERS: dict[str, str] = _CONTEXT["agentgateway_docker_hindsight_roster_tiers"]
BANKS: dict[str, str] = _CONTEXT["agentgateway_docker_hindsight_agent_banks"]
TRUSTED, TRUSTED_PEER = "hermes-brain", "hermes-ops"
UNTRUSTED, UNTRUSTED_PEER = "paperclip", "scout-open"
_UNSET = object()


def _request(
    agent: str | None,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    tier: Any = _UNSET,
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "request": {"method": method, "path": path, "headers": {k.lower(): v for k, v in (headers or {}).items()}}
    }
    if agent is not None:
        # The gateway flattens the matched key's metadata onto apiKey; a roster agent
        # without a tier has no `tier` field at all.
        meta: dict[str, Any] = {"agent": agent}
        resolved = TIERS.get(agent) if tier is _UNSET else tier
        if resolved is not None:
            meta["tier"] = resolved
        env["apiKey"] = meta
    return env


def _allowed(
    rules: list[dict[str, str]], agent: str | None, method: str, path: str, tier: Any = _UNSET, **headers: str
) -> bool:
    request = _request(agent, method, path, {k.replace("_", "-"): v for k, v in headers.items()}, tier)
    return authorize(rules, request)[0]


# --- rendered shape ---------------------------------------------------------
