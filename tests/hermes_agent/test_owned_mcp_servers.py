"""Roster-owned MCP routes use the roster owner's gateway key."""

from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

from test_profile_contract import ROLE_ROOT, _RENDER_CONTEXT, _defaults, _jinja_env
from test_profile_env_parity import _context as _env_context
from test_profile_env_parity import _jinja_env as _env_jinja_env
from test_profile_env_parity import _env_values


ROSTER = {"splunk": "example-agent", "cribl": "example-agent"}
GATEWAY_KEY_FIELD = "example_agent_agentgateway_key"
GATEWAY_KEY = "example-agent-gateway-key"


def _gateway_key_field(identity: str) -> str:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main" / "165-owned-mcp.yml").read_text())
    return Environment().from_string(defaults["hermes_agent_gateway_key_field"]).render(
        hermes_agent_id=identity
    )


def _owned_slugs(identity: str) -> list[str]:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main" / "165-owned-mcp.yml").read_text())
    env = Environment()
    env.filters["dict2items"] = lambda values: [
        {"key": key, "value": value} for key, value in values.items()
    ]
    rendered = env.from_string(defaults["hermes_agent_owned_mcp_slugs"]).render(
        ai_agent_owner=ROSTER,
        hermes_agent_id=identity,
    )
    return yaml.safe_load(rendered)


def _config_context(identity: str) -> dict:
    context = dict(_defaults())
    context.update(_RENDER_CONTEXT)
    context.update(
        hermes_agent_id=identity,
        hermes_agent_owned_mcp_slugs=_owned_slugs(identity),
        hermes_agent_gateway_key_field=_gateway_key_field(identity),
        codex_runner_user="example-user",
        codex_runner_mcp_command="codex mcp",
    )
    return context


def _env_owner_context(identity: str) -> dict:
    context = _env_context()
    context.update(
        hermes_agent_id=identity,
        hermes_agent_owned_mcp_slugs=_owned_slugs(identity),
        hermes_agent_gateway_key_field=_gateway_key_field(identity),
        openbao_secrets_by_domain={identity: {GATEWAY_KEY_FIELD: GATEWAY_KEY}},
    )
    return context


def _mcp_servers(rendered: str) -> dict:
    section = rendered.split("mcp_servers:\n", 1)[1].split("\ntimezone:", 1)[0]
    return yaml.safe_load("mcp_servers:\n" + section)["mcp_servers"]


def test_owned_splunk_route_uses_the_roster_agents_key() -> None:
    env = _jinja_env()
    owner = _config_context("example-agent")
    rendered = env.get_template("config.yaml.j2").render(**owner)
    servers = _mcp_servers(rendered)
    server = servers["splunk"]

    assert server["url"] == "${SPLUNK_MCP_URL}"
    assert server["headers"] == {
        "Authorization": "Bearer ${SPLUNK_MCP_TOKEN}",
        "X-Agent-Key": "${HERMES_AGENT_GATEWAY_KEY}",
    }
    assert "cribl" not in servers
    assert GATEWAY_KEY not in rendered

    profile = {
        "name": "splunk-admin",
        "description": "fixture",
        "mcp": ["splunk", "cribl"],
        "env": ["splunk"],
    }
    profile_context = _config_context("example-agent")
    profile_context["hermes_agent_profile"] = profile
    profile_servers = yaml.safe_load(
        env.get_template("config-profile.yaml.j2").render(**profile_context)
    )["mcp_servers"]
    profile_server = profile_servers["splunk"]
    assert profile_server["headers"]["X-Agent-Key"] == "${HERMES_AGENT_GATEWAY_KEY}"
    assert "cribl" not in profile_servers

    profile_context["hermes_agent_profile"] = {
        "name": "github-maint", "description": "fixture", "mcp": ["docs"], "env": []
    }
    ungranted_servers = yaml.safe_load(
        env.get_template("config-profile.yaml.j2").render(**profile_context)
    )["mcp_servers"]
    assert "splunk" not in ungranted_servers


def test_owned_gateway_key_stays_in_owner_env_and_ungranted_profile_is_blank() -> None:
    env = _env_jinja_env()
    owner_context = _env_owner_context("example-agent")
    owner_env = env.get_template("hermes-env.j2").render(**owner_context)
    assert f"HERMES_AGENT_GATEWAY_KEY={GATEWAY_KEY}" in owner_env

    profile = {"name": "splunk-admin", "description": "fixture", "mcp": ["splunk"], "env": ["splunk"]}
    profile_values = _env_values(
        env.get_template("hermes-env-profile.j2").render(
            hermes_agent_profile=profile, **owner_context
        )
    )
    assert profile_values["HERMES_AGENT_GATEWAY_KEY"] == GATEWAY_KEY

    profile["mcp"] = ["docs"]
    profile_values = _env_values(
        env.get_template("hermes-env-profile.j2").render(
            hermes_agent_profile=profile, **owner_context
        )
    )
    assert profile_values["HERMES_AGENT_GATEWAY_KEY"] == ""


def test_legacy_identities_receive_no_roster_gateway_key() -> None:
    env = _jinja_env()
    for identity in ("hermes", "hermes-private", "donna"):
        rendered = env.get_template("config.yaml.j2").render(**_config_context(identity))
        servers = _mcp_servers(rendered)
        server = servers["splunk"]
        assert "X-Agent-Key" not in server["headers"]
        assert "cribl" not in servers

        profile_context = _config_context(identity)
        profile_context["hermes_agent_profile"] = {
            "name": "splunk-admin", "description": "fixture", "mcp": ["splunk"], "env": ["splunk"]
        }
        profile_server = yaml.safe_load(
            env.get_template("config-profile.yaml.j2").render(**profile_context)
        )["mcp_servers"]["splunk"]
        assert "X-Agent-Key" not in profile_server["headers"]

        owner_env = _env_context()
        owner_env.update(
            hermes_agent_id=identity,
            hermes_agent_owned_mcp_slugs=_owned_slugs(identity),
            hermes_agent_gateway_key_field=_gateway_key_field(identity),
        )
        env_rendered = _env_jinja_env().get_template("hermes-env.j2").render(**owner_env)
        assert "HERMES_AGENT_GATEWAY_KEY=" not in env_rendered


def test_owned_header_and_env_lines_hold_under_ansible_trim_blocks() -> None:
    # The template module renders with trim_blocks on; inline tags there join lines.
    config_env = _jinja_env()
    config_env.trim_blocks = True
    env_env = _env_jinja_env()
    env_env.trim_blocks = True
    profile = {"name": "splunk-admin", "description": "fixture", "mcp": ["splunk"], "env": ["splunk"]}
    bearer = 'Authorization: "Bearer ${SPLUNK_MCP_TOKEN}"\n'
    header = bearer + '      X-Agent-Key: "${HERMES_AGENT_GATEWAY_KEY}"\n'
    for identity, owner in (("example-agent", True), ("hermes", False)):
        context = _config_context(identity)
        context["hermes_agent_profile"] = profile
        for template in ("config.yaml.j2", "config-profile.yaml.j2"):
            rendered = config_env.get_template(template).render(**context)
            assert bearer in rendered, (template, identity)
            assert (header in rendered) is owner, (template, identity)

        env_context = _env_owner_context(identity) if owner else _env_context()
        env_rendered = env_env.get_template("hermes-env.j2").render(**env_context)
        line_after_token = env_rendered.split("SPLUNK_MCP_TOKEN=", 1)[1].split("\n", 2)[1]
        expected = f"HERMES_AGENT_GATEWAY_KEY={GATEWAY_KEY}" if owner else ""
        assert line_after_token == expected, identity
