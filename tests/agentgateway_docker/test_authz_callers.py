"""Caller-key validation and keyless caller rendering."""

from __future__ import annotations

from typing import Any

import yaml
from test_authz_render import (
    AGENTS,
    ENV,
    KEYS,
    ROLE_DEFAULTS,
    TASKS,
    _render,
    _render_key_fields,
    _render_map,
    _routes,
)


def test_missing_roster_key_fails_the_role_assert_expression() -> None:
    tasks = yaml.safe_load(TASKS.read_text())
    validation = next(
        task
        for task in tasks
        if task.get("name") == "Validate that every roster agent has a gateway API key"
    )
    condition = validation["ansible.builtin.assert"]["that"][0]
    roster_keys = _render_map(
        ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
        ai_agent_gateway_key_fields=_render_key_fields(AGENTS),
        openbao_secrets_by_domain={
            "agentgateway-agent-keys": {
                "splunk_heimdall_agentgateway_key": KEYS["splunk_heimdall_agentgateway_key"]
            }
        },
    )
    assert (
        ENV.compile_expression(condition)(
            item={"key": "splunk-heimdall"}, agentgateway_docker_roster_keys=roster_keys
        )
        is True
    )
    assert (
        ENV.compile_expression(condition)(
            item={"key": "other-agent"}, agentgateway_docker_roster_keys=roster_keys
        )
        is False
    )


def _rendered_caller_keys(rendered: str) -> list[dict[str, Any]]:
    keys: list[dict[str, Any]] = []
    for route in _routes(rendered):
        keys.extend(route.get("policies", {}).get("apiKey", {}).get("keys", []))
    return keys


def test_keyless_callers_are_omitted_from_rendered_config() -> None:
    # other-agent holds only whitespace and the registry caller has no leaf: both are keyless.
    keys = {
        "splunk_heimdall_agentgateway_key": KEYS["splunk_heimdall_agentgateway_key"],
        "other_agent_agentgateway_key": "   ",
    }
    rendered = _render(
        AGENTS,
        {},
        keys,
        registry={"registry-unseeded": {"trust_tier": "untrusted"}},
    )
    rendered_keys = _rendered_caller_keys(rendered)

    assert rendered_keys
    assert {key["metadata"]["agent"] for key in rendered_keys} == {"splunk-heimdall"}
    assert all(key["key"] for key in rendered_keys)
    assert 'key: ""' not in rendered


def test_gateway_reader_domain_is_required_when_gateway_callers_exist() -> None:
    tasks = yaml.safe_load(TASKS.read_text())
    presence = next(
        task
        for task in tasks
        if task.get("name") == "Require the agentgateway-agent-keys domain when gateway callers exist"
    )
    condition = presence["ansible.builtin.assert"]["that"][0]
    when = presence["when"][0]

    assert ENV.compile_expression(condition)(openbao_secrets_by_domain={}) is False
    assert (
        ENV.compile_expression(condition)(openbao_secrets_by_domain={"agentgateway-agent-keys": {}})
        is True
    )
    callers = {"splunk-heimdall": "splunk_heimdall_agentgateway_key"}
    assert ENV.compile_expression(when)(ai_agent_gateway_key_fields=callers) is True
    assert ENV.compile_expression(when)(ai_agent_gateway_key_fields={}) is False
