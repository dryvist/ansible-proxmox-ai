"""Caller facade: the /agents route, the strict /vikunja route, and the role's fail-closed asserts."""

from __future__ import annotations

import re
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
    _route_for_path,
    _routes,
)

OWNERS = {"splunk": "splunk-heimdall"}
FACADE_ON = {
    "agentgateway_docker_facade_enabled": True,
    "agentgateway_docker_facade_callers": ["splunk-heimdall"],
}
CREDENTIAL_HEADERS = ["authorization", "proxy-authorization", "cookie", "x-agent-key"]
VIKUNJA_HOST = "http://mcp-vikunja:8002/mcp"
SHARED_MODE_PATHS = ("/memory", "/docs", "/nautobot")
FACADE_TASK = "Validate the caller facade inputs"
EXCEPTION_TASK = "Validate the MCP owner exceptions"
OWNER_TASK = "Validate that every MCP target has an owner or a recorded exception"
ROSTER_KEYS = _render_map(
    ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
    ai_agent_gateway_key_fields=_render_key_fields(AGENTS),
    openbao_secrets_by_domain={"agentgateway-agent-keys": KEYS},
)


def _facade_routes(**extra: Any) -> list[dict[str, Any]]:
    return _routes(_render(AGENTS, OWNERS, KEYS, extra={**FACADE_ON, **extra}))


def _exact(route: dict[str, Any]) -> str | None:
    return route["matches"][0]["path"].get("exact")


def _task(name: str) -> dict[str, Any]:
    return next(task for task in yaml.safe_load(TASKS.read_text()) if task.get("name") == name)


def _strip_braces(expression: str) -> str:
    return expression.strip().removeprefix("{{").removesuffix("}}").strip()


def _task_passes(task: dict[str, Any], **context: Any) -> bool:
    """Evaluate a role assert as Ansible does: `when` gates it, `vars` and `loop` resolve first."""
    if "when" in task and not ENV.compile_expression(task["when"])(**context):
        return True
    resolved = dict(context)
    for name, expression in task.get("vars", {}).items():
        resolved[name] = ENV.compile_expression(_strip_braces(expression))(**resolved)
    items: Any = [None]
    if "loop" in task:
        items = ENV.compile_expression(_strip_braces(task["loop"]))(**resolved)
    for item in items:
        for condition in task["ansible.builtin.assert"]["that"]:
            if not ENV.compile_expression(condition)(item=item, **resolved):
                return False
    return True


def test_facade_is_off_by_default_and_renders_no_agents_route() -> None:
    assert ROLE_DEFAULTS["agentgateway_docker_facade_enabled"] is False
    assert ROLE_DEFAULTS["agentgateway_docker_facade_callers"] == []
    routes = _routes(_render(AGENTS, OWNERS, KEYS))

    assert "/agents" not in [_exact(route) for route in routes]
    vikunja = _route_for_path(routes, "/vikunja")
    assert set(vikunja["policies"]) == {"apiKey"}
    assert vikunja["policies"]["apiKey"]["mode"] == "optional"


def test_callers_alone_do_not_change_the_render_while_disabled() -> None:
    off = _routes(_render(AGENTS, OWNERS, KEYS))
    callers_only = _routes(
        _render(AGENTS, OWNERS, KEYS, extra={"agentgateway_docker_facade_callers": ["splunk-heimdall"]})
    )

    assert callers_only == off


def test_enabled_facade_route_is_strict_scoped_stripping_and_single_backend() -> None:
    facade = _route_for_path(_facade_routes(), "/agents")
    policies = facade["policies"]

    assert set(policies) == {"apiKey", "authorization", "requestHeaderModifier"}
    assert policies["apiKey"]["mode"] == "strict"
    assert policies["apiKey"]["location"] == {"header": {"name": "X-Agent-Key"}}
    assert policies["apiKey"]["keys"] == _route_for_path(_facade_routes(), "/splunk")["policies"]["apiKey"]["keys"]
    assert policies["authorization"] == {"rules": [{"allow": "apiKey.agent == 'splunk-heimdall'"}]}
    assert policies["requestHeaderModifier"] == {"remove": CREDENTIAL_HEADERS}
    assert ROLE_DEFAULTS["agentgateway_docker_caller_credential_headers"] == CREDENTIAL_HEADERS
    assert len(facade["backends"]) == 1
    targets = facade["backends"][0]["mcp"]["targets"]
    assert [target["name"] for target in targets] == ["vikunja"]
    assert targets[0]["mcp"] == {"host": VIKUNJA_HOST}


def test_enabled_facade_path_is_configurable() -> None:
    routes = _facade_routes(agentgateway_docker_facade_path="/agents-canary")

    assert "/agents" not in [_exact(route) for route in routes]
    assert _route_for_path(routes, "/agents-canary")["policies"]["apiKey"]["mode"] == "strict"


def test_enabled_facade_makes_vikunja_strict_with_the_same_caller_list() -> None:
    routes = _facade_routes()
    vikunja = _route_for_path(routes, "/vikunja")

    assert vikunja["policies"]["apiKey"]["mode"] == "strict"
    assert vikunja["policies"]["apiKey"]["location"] == {"header": {"name": "X-Agent-Key"}}
    assert vikunja["policies"]["authorization"] == _route_for_path(routes, "/agents")["policies"]["authorization"]
    assert vikunja["policies"]["requestHeaderModifier"] == {"remove": CREDENTIAL_HEADERS}
    assert vikunja["backends"][0]["mcp"]["targets"][0]["mcp"] == {"host": VIKUNJA_HOST}


def test_enabled_facade_leaves_the_other_mcp_routes_on_the_shared_mode() -> None:
    routes = _facade_routes()

    for path in SHARED_MODE_PATHS:
        policies = _route_for_path(routes, path)["policies"]
        assert policies["apiKey"]["mode"] == "optional"
        assert "authorization" not in policies


def test_image_is_pinned_by_tag_and_digest() -> None:
    assert re.fullmatch(
        r"cr\.agentgateway\.dev/agentgateway:v1\.6\.0@sha256:[0-9a-f]{64}",
        ROLE_DEFAULTS["agentgateway_docker_image"],
    )


FACADE_CONTEXT = {
    "agentgateway_docker_facade_enabled": True,
    "agentgateway_docker_facade_callers": ["splunk-heimdall"],
    "agentgateway_docker_roster_keys": ROSTER_KEYS,
    "agentgateway_docker_vikunja_mcp_enabled": True,
    "agentgateway_docker_vikunja_read_token": "example-vikunja-token",
}


def test_facade_inputs_pass_for_a_roster_backed_caller() -> None:
    assert _task_passes(_task(FACADE_TASK), **FACADE_CONTEXT)


def test_facade_assert_fails_for_empty_callers() -> None:
    assert not _task_passes(_task(FACADE_TASK), **{**FACADE_CONTEXT, "agentgateway_docker_facade_callers": []})


def test_facade_assert_fails_for_an_unknown_caller() -> None:
    callers = ["splunk-heimdall", "ghost-agent"]

    assert not _task_passes(_task(FACADE_TASK), **{**FACADE_CONTEXT, "agentgateway_docker_facade_callers": callers})


def test_facade_assert_fails_without_the_vikunja_sidecar() -> None:
    assert not _task_passes(
        _task(FACADE_TASK), **{**FACADE_CONTEXT, "agentgateway_docker_vikunja_read_token": ""}
    )


def test_disabled_facade_skips_its_assert() -> None:
    disabled = {**FACADE_CONTEXT, "agentgateway_docker_facade_enabled": False, "agentgateway_docker_facade_callers": []}

    assert _task_passes(_task(FACADE_TASK), **disabled)


OWNER_CONTEXT = {
    "agentgateway_docker_mcp_targets": ROLE_DEFAULTS["agentgateway_docker_mcp_targets"],
    "ai_agent_owner": OWNERS,
    "agentgateway_docker_owner_exceptions": [],
}
RECORDED_EXCEPTION = {
    "name": "context7",
    "reason": "pre-B6 route, migrate with caller-key receipt",
    "cutover_task": "Vikunja 6201",
}


def test_ownerless_target_without_an_exception_fails_the_owner_assert() -> None:
    assert not _task_passes(_task(OWNER_TASK), **OWNER_CONTEXT)


def test_ownerless_target_with_a_recorded_exception_passes() -> None:
    recorded = {**OWNER_CONTEXT, "agentgateway_docker_owner_exceptions": [RECORDED_EXCEPTION]}

    assert _task_passes(_task(OWNER_TASK), **recorded)
    assert _task_passes(_task(EXCEPTION_TASK), **recorded)


def test_owned_targets_need_no_exception() -> None:
    owned = {**OWNER_CONTEXT, "ai_agent_owner": {"splunk": "splunk-heimdall", "context7": "other-agent"}}

    assert _task_passes(_task(OWNER_TASK), **owned)


def test_exception_without_a_reason_or_cutover_task_fails() -> None:
    no_reason = {**RECORDED_EXCEPTION, "reason": ""}
    no_cutover = {**RECORDED_EXCEPTION, "cutover_task": "  "}

    for exception in (no_reason, no_cutover):
        context = {**OWNER_CONTEXT, "agentgateway_docker_owner_exceptions": [exception]}
        assert not _task_passes(_task(EXCEPTION_TASK), **context)
