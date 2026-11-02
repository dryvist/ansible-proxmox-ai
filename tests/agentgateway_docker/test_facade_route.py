"""Caller facade: the /agents route, staged /vikunja auth, and fail-closed asserts."""

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
CLAUDE_CODE = "claude-code"
REGISTRY = {CLAUDE_CODE: {"trust_tier": "trusted"}}
FACADE_KEYS = {**KEYS, "claude_code_agentgateway_key": "example-key-claude-code"}
FACADE_ON = {
    "agentgateway_docker_facade_enabled": True,
    "agentgateway_docker_facade_callers": [CLAUDE_CODE],
}
CREDENTIAL_HEADERS = ["authorization", "proxy-authorization", "cookie", "x-agent-key"]
VIKUNJA_HOST = "http://mcp-vikunja:8002/mcp"
SHARED_MODE_PATHS = ("/memory", "/docs", "/nautobot")
FACADE_TASK = "Validate the caller facade inputs"
EXCEPTION_TASK = "Validate the MCP owner exceptions"
OWNER_TASK = "Validate that every MCP target has an owner or a recorded exception"
ROSTER_KEYS = _render_map(
    ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
    ai_agent_gateway_key_fields=_render_key_fields(AGENTS, REGISTRY),
    openbao_secrets_by_domain={"agentgateway-agent-keys": FACADE_KEYS},
)


def _facade_routes(**extra: Any) -> list[dict[str, Any]]:
    return _routes(
        _render(
            AGENTS,
            OWNERS,
            FACADE_KEYS,
            extra={**FACADE_ON, **extra},
            registry=REGISTRY,
        )
    )


def _exact(route: dict[str, Any]) -> str | None:
    return route["matches"][0]["path"].get("exact")


def _raw_route(rendered: str, path: str) -> str:
    starts = list(re.finditer(r"(?m)^    - matches:\n", rendered))
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(rendered)
        block = rendered[start.start() : end]
        if f"          exact: {path}\n" in block:
            return block
    raise AssertionError(f"rendered config has no exact route for {path}")


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


def test_facade_is_enabled_by_default_for_the_registered_claude_code_caller() -> None:
    assert ROLE_DEFAULTS["agentgateway_docker_facade_enabled"] is True
    assert ROLE_DEFAULTS["agentgateway_docker_facade_callers"] == [CLAUDE_CODE]
    rendered = _render(
        AGENTS,
        OWNERS,
        FACADE_KEYS,
        extra={
            "agentgateway_docker_facade_enabled": ROLE_DEFAULTS["agentgateway_docker_facade_enabled"],
            "agentgateway_docker_facade_callers": ROLE_DEFAULTS["agentgateway_docker_facade_callers"],
        },
        registry=REGISTRY,
    )
    routes = _routes(rendered)

    assert "/agents" in [_exact(route) for route in routes]
    facade = _route_for_path(routes, "/agents")
    assert facade["policies"]["apiKey"]["mode"] == "strict"
    assert facade["policies"]["authorization"] == {
        "rules": [{"allow": "apiKey.agent == 'claude-code'"}]
    }
    vikunja = _route_for_path(routes, "/vikunja")
    assert set(vikunja["policies"]) == {"apiKey", "requestHeaderModifier"}
    assert vikunja["policies"]["apiKey"]["mode"] == "optional"
    assert "authorization" not in vikunja["policies"]
    assert vikunja["policies"]["requestHeaderModifier"] == {"remove": CREDENTIAL_HEADERS}


def test_callers_alone_do_not_change_the_render_while_disabled() -> None:
    off = _routes(
        _render(
            AGENTS,
            OWNERS,
            FACADE_KEYS,
            extra={"agentgateway_docker_facade_enabled": False, "agentgateway_docker_facade_callers": []},
            registry=REGISTRY,
        )
    )
    callers_only = _routes(
        _render(
            AGENTS,
            OWNERS,
            FACADE_KEYS,
            extra={"agentgateway_docker_facade_enabled": False, "agentgateway_docker_facade_callers": [CLAUDE_CODE]},
            registry=REGISTRY,
        )
    )

    assert callers_only == off


def test_enabled_facade_route_is_strict_scoped_stripping_and_single_backend() -> None:
    facade = _route_for_path(_facade_routes(), "/agents")
    policies = facade["policies"]

    assert set(policies) == {"apiKey", "authorization", "requestHeaderModifier"}
    assert policies["apiKey"]["mode"] == "strict"
    assert policies["apiKey"]["location"] == {"header": {"name": "X-Agent-Key"}}
    assert policies["apiKey"]["keys"] == _route_for_path(_facade_routes(), "/splunk")["policies"]["apiKey"]["keys"]
    assert policies["authorization"] == {"rules": [{"allow": "apiKey.agent == 'claude-code'"}]}
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


def test_vikunja_route_is_byte_identical_with_facade_enabled_or_disabled() -> None:
    disabled = _render(
        AGENTS,
        OWNERS,
        FACADE_KEYS,
        extra={"agentgateway_docker_facade_enabled": False, "agentgateway_docker_facade_callers": []},
        registry=REGISTRY,
    )
    enabled = _render(AGENTS, OWNERS, FACADE_KEYS, extra=FACADE_ON, registry=REGISTRY)

    assert _raw_route(enabled, "/vikunja") == _raw_route(disabled, "/vikunja")


def test_vikunja_route_keeps_optional_api_key_auth_separate_from_facade_auth() -> None:
    routes = _facade_routes()
    vikunja = _route_for_path(routes, "/vikunja")
    api_key = vikunja["policies"]["apiKey"]
    assert api_key["mode"] == ROLE_DEFAULTS["agentgateway_docker_mcp_route_auth_mode"] == "optional"
    assert api_key["location"] == {"header": {"name": "X-Agent-Key"}}
    assert api_key["keys"] == _route_for_path(routes, "/splunk")["policies"]["apiKey"]["keys"]
    assert "authorization" not in vikunja["policies"]
    assert vikunja["backends"][0]["mcp"]["targets"][0]["mcp"] == {"host": VIKUNJA_HOST}


def test_vikunja_strips_caller_credentials_with_facade_enabled_or_disabled() -> None:
    for enabled, callers in ((False, []), (True, [CLAUDE_CODE])):
        rendered = _render(
            AGENTS,
            OWNERS,
            FACADE_KEYS,
            extra={"agentgateway_docker_facade_enabled": enabled, "agentgateway_docker_facade_callers": callers},
            registry=REGISTRY,
        )
        vikunja = _route_for_path(_routes(rendered), "/vikunja")

        assert vikunja["policies"]["requestHeaderModifier"] == {"remove": CREDENTIAL_HEADERS}


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
    "agentgateway_docker_facade_callers": [CLAUDE_CODE],
    "agentgateway_docker_roster_keys": ROSTER_KEYS,
    "agentgateway_docker_vikunja_mcp_enabled": True,
    "agentgateway_docker_vikunja_read_token": "example-vikunja-token",
}


def test_facade_inputs_pass_for_a_roster_backed_caller() -> None:
    assert _task_passes(_task(FACADE_TASK), **FACADE_CONTEXT)


def test_facade_assert_fails_for_empty_callers() -> None:
    assert not _task_passes(_task(FACADE_TASK), **{**FACADE_CONTEXT, "agentgateway_docker_facade_callers": []})


def test_facade_assert_fails_for_an_unknown_caller() -> None:
    callers = [CLAUDE_CODE, "ghost-agent"]

    assert not _task_passes(_task(FACADE_TASK), **{**FACADE_CONTEXT, "agentgateway_docker_facade_callers": callers})


def test_facade_assert_fails_without_the_vikunja_sidecar() -> None:
    assert not _task_passes(
        _task(FACADE_TASK), **{**FACADE_CONTEXT, "agentgateway_docker_vikunja_read_token": ""}
    )


def test_disabled_facade_skips_its_assert() -> None:
    disabled = {**FACADE_CONTEXT, "agentgateway_docker_facade_enabled": False, "agentgateway_docker_facade_callers": []}

    assert _task_passes(_task(FACADE_TASK), **disabled)


def test_facade_input_assert_precedes_the_first_role_write() -> None:
    tasks = yaml.safe_load(TASKS.read_text())
    names = [task.get("name") for task in tasks]
    assert names.index(FACADE_TASK) < names.index("Create agentgateway data directory")


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
