"""The profile facade canary is a default-off, single-client render."""

from __future__ import annotations

import yaml
from jinja2 import Environment

from test_owned_mcp_servers import (
    GATEWAY_KEY,
    GATEWAY_KEY_FIELD,
    _config_context,
    _env_owner_context,
)
from test_profile_contract import ROLE_ROOT, _defaults, _jinja_env
from test_profile_env_parity import _env_values, _jinja_env as _env_jinja_env


PROFILE = {
    "name": "orchestrator-fixture",
    "description": "synthetic bounded canary",
    "mcp": ["vikunja", "docs", "grep"],
    "env": [],
}


def _profile_config(profile: dict) -> str:
    context = _config_context("example-agent", owner_map={})
    context["hermes_agent_profile"] = profile
    return _jinja_env().get_template("config-profile.yaml.j2").render(**context)


def _assert_environment():
    env = Environment()
    env.filters["bool"] = bool
    env.filters["string"] = str
    env.filters["trim"] = lambda value: str(value).strip()
    return env


def _canary_assert_passes(context: dict) -> bool:
    task = yaml.safe_load(
        (ROLE_ROOT / "tasks" / "assert_profile_facade_canary.yml").read_text()
    )[0]
    environment = _assert_environment()
    for profile in context["hermes_agent_profiles"]:
        if not profile.get("facade_canary", False):
            continue
        variables = {**context, "item": profile}
        if not all(
            environment.compile_expression(condition)(**variables)
            for condition in task["ansible.builtin.assert"]["that"]
        ):
            return False
    return True


def test_profile_selector_defaults_off_and_preserves_legacy_render() -> None:
    orchestrator = next(
        profile for profile in _defaults()["hermes_agent_profiles"]
        if profile["name"] == "orchestrator"
    )
    assert orchestrator["facade_canary"] is False

    absent = _profile_config(PROFILE)
    explicit_off = _profile_config({**PROFILE, "facade_canary": False})
    assert absent == explicit_off
    servers = yaml.safe_load(absent)["mcp_servers"]
    assert set(servers) == {"docs", "grep", "vikunja"}


def test_enabled_canary_renders_only_native_keyed_agents_facade() -> None:
    profile = {**PROFILE, "facade_canary": True}
    rendered = _profile_config(profile)
    servers = yaml.safe_load(rendered)["mcp_servers"]

    assert set(servers) == {"agent-facade"}
    assert servers["agent-facade"] == {
        "timeout": 180,
        "url": "https://mcp.example.com/agents",
        "headers": {"X-Agent-Key": "${HERMES_AGENT_GATEWAY_KEY}"},
    }
    assert GATEWAY_KEY not in rendered

    context = _env_owner_context("example-agent", owner_map={})
    env_rendered = _env_jinja_env().get_template("hermes-env-profile.j2").render(
        hermes_agent_profile=profile, **context
    )
    assert _env_values(env_rendered)["HERMES_AGENT_GATEWAY_KEY"] == GATEWAY_KEY


def test_profile_env_default_off_is_byte_identical_and_keeps_existing_blanking() -> None:
    context = _env_owner_context("example-agent")
    absent = _env_jinja_env().get_template("hermes-env-profile.j2").render(
        hermes_agent_profile=PROFILE, **context
    )
    explicit_off = _env_jinja_env().get_template("hermes-env-profile.j2").render(
        hermes_agent_profile={**PROFILE, "facade_canary": False}, **context
    )

    assert absent == explicit_off
    assert _env_values(absent)["HERMES_AGENT_GATEWAY_KEY"] == ""


def test_canary_assert_requires_existing_identity_field_and_nonempty_value() -> None:
    base = {
        "hermes_agent_profiles": [{**PROFILE, "facade_canary": True}],
        "hermes_agent_vikunja_mcp_enabled": True,
        "hermes_agent_id": "example-agent",
        "hermes_agent_gateway_key_field": GATEWAY_KEY_FIELD,
        "ai_agent_gateway_key_fields": {"example-agent": GATEWAY_KEY_FIELD},
        "openbao_secrets_by_domain": {
            "example-agent": {GATEWAY_KEY_FIELD: GATEWAY_KEY}
        },
    }
    assert _canary_assert_passes(base)
    assert not _canary_assert_passes(
        {**base, "ai_agent_gateway_key_fields": {}}
    )
    assert not _canary_assert_passes(
        {**base, "openbao_secrets_by_domain": {"example-agent": {}}}
    )
    assert not _canary_assert_passes(
        {**base, "hermes_agent_vikunja_mcp_enabled": False}
    )


def test_disabled_canary_does_not_require_a_caller_binding() -> None:
    assert _canary_assert_passes(
        {
            "hermes_agent_profiles": [{**PROFILE, "facade_canary": False}],
            "hermes_agent_vikunja_mcp_enabled": True,
            "hermes_agent_id": "hermes",
            "hermes_agent_gateway_key_field": "",
            "ai_agent_gateway_key_fields": {},
            "openbao_secrets_by_domain": {},
        }
    )
