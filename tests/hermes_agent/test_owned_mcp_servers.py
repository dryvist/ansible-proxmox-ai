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
SPLUNK_FALLBACK = "example-splunk-token"


def _gateway_key_field(identity: str) -> str:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main" / "165-owned-mcp.yml").read_text())
    return Environment().from_string(defaults["hermes_agent_gateway_key_field"]).render(
        hermes_agent_id=identity,
        ai_agent_gateway_key_fields={"example-agent": GATEWAY_KEY_FIELD},
    )


def _owned_slugs(identity: str, roster: dict[str, str] = ROSTER) -> list[str]:
    defaults = yaml.safe_load((ROLE_ROOT / "defaults" / "main" / "165-owned-mcp.yml").read_text())
    env = Environment()
    env.filters["dict2items"] = lambda values: [
        {"key": key, "value": value} for key, value in values.items()
    ]
    rendered = env.from_string(defaults["hermes_agent_owned_mcp_slugs"]).render(
        ai_agent_owner=roster,
        hermes_agent_id=identity,
    )
    return yaml.safe_load(rendered)


def _splunk_access(identity: str, owner_map: dict[str, str]) -> bool:
    raw = _defaults()["hermes_agent_splunk_access"]
    rendered = Environment().from_string(raw).render(
        ai_agent_owner=owner_map,
        hermes_agent_id=identity,
    )
    return yaml.safe_load(rendered)


def _render_setting(raw: str, **context: object) -> str:
    env = Environment()
    env.filters["bool"] = bool
    env.globals["lookup"] = lambda plugin, name: (
        "example.com" if name == "PROXMOX_SUBDOMAIN" else SPLUNK_FALLBACK
    )
    env.globals["undef"] = lambda message: (_ for _ in ()).throw(ValueError(message))
    return env.from_string(raw).render(**context)


def _splunk_url(identity: str, owner_map: dict[str, str]) -> str:
    access = _splunk_access(identity, owner_map)
    return _render_setting(
        _defaults()["hermes_agent_splunk_mcp_url"],
        hermes_agent_splunk_access=access,
    )


def _splunk_token(identity: str, owner_map: dict[str, str]) -> str:
    group_vars = yaml.safe_load(
        (ROLE_ROOT.parents[1] / "inventory/group_vars/hermes_agent_group/00-agent-settings.yml").read_text()
    )
    return _render_setting(
        group_vars["hermes_agent_splunk_mcp_token"],
        hermes_agent_splunk_access=_splunk_access(identity, owner_map),
        hermes_agent_mcp_bao={},
    )


def _config_context(identity: str, owner_map: dict[str, str] = ROSTER) -> dict:
    context = dict(_defaults())
    context.update(_RENDER_CONTEXT)
    access = _splunk_access(identity, owner_map)
    context.update(
        hermes_agent_id=identity,
        hermes_agent_splunk_access=access,
        hermes_agent_splunk_mcp_url=_splunk_url(identity, owner_map),
        hermes_agent_owned_mcp_slugs=_owned_slugs(identity, owner_map),
        hermes_agent_gateway_key_field=_gateway_key_field(identity),
        codex_runner_user="example-user",
        codex_runner_mcp_command="codex mcp",
    )
    return context


def _env_owner_context(identity: str, owner_map: dict[str, str] = ROSTER) -> dict:
    context = _env_context()
    context.update(
        hermes_agent_id=identity,
        hermes_agent_splunk_access=_splunk_access(identity, owner_map),
        hermes_agent_splunk_mcp_url=_splunk_url(identity, owner_map),
        hermes_agent_splunk_mcp_token=_splunk_token(identity, owner_map),
        hermes_agent_owned_mcp_slugs=_owned_slugs(identity, owner_map),
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
    owner_values = _env_values(owner_env)
    assert owner_values["SPLUNK_MCP_URL"] == "https://mcp.example.com/splunk"
    assert owner_values["SPLUNK_MCP_TOKEN"] == SPLUNK_FALLBACK

    profile = {"name": "splunk-admin", "description": "fixture", "mcp": ["splunk"], "env": ["splunk"]}
    profile_values = _env_values(
        env.get_template("hermes-env-profile.j2").render(
            hermes_agent_profile=profile, **owner_context
        )
    )
    assert profile_values["SPLUNK_MCP_URL"] == "https://mcp.example.com/splunk"
    assert profile_values["SPLUNK_MCP_TOKEN"] == SPLUNK_FALLBACK
    assert profile_values["HERMES_AGENT_GATEWAY_KEY"] == GATEWAY_KEY

    profile["mcp"] = ["docs"]
    profile_values = _env_values(
        env.get_template("hermes-env-profile.j2").render(
            hermes_agent_profile=profile, **owner_context
        )
    )
    assert profile_values["HERMES_AGENT_GATEWAY_KEY"] == ""


def test_legacy_identities_receive_no_splunk_when_an_owner_is_registered() -> None:
    env = _jinja_env()
    for identity in ("hermes", "hermes-private", "donna"):
        context = _config_context(identity)
        assert context["hermes_agent_splunk_mcp_url"] == ""
        assert _splunk_token(identity, ROSTER) == ""
        rendered = env.get_template("config.yaml.j2").render(**context)
        servers = _mcp_servers(rendered)
        assert "splunk" not in servers
        assert "cribl" not in servers

        profile_context = _config_context(identity)
        profile_context["hermes_agent_profile"] = {
            "name": "splunk-admin", "description": "fixture", "mcp": ["splunk"], "env": ["splunk"]
        }
        profile_rendered = yaml.safe_load(
            env.get_template("config-profile.yaml.j2").render(**profile_context)
        )
        assert "splunk" not in profile_rendered.get("mcp_servers", {})

        owner_env = _env_owner_context(identity)
        env_rendered = _env_jinja_env().get_template("hermes-env.j2").render(**owner_env)
        profile_env = _env_jinja_env().get_template("hermes-env-profile.j2").render(
            hermes_agent_profile={
                "name": "splunk-admin", "description": "fixture",
                "mcp": ["splunk"], "env": ["splunk"],
            },
            **owner_env,
        )
        assert not {"SPLUNK_MCP_URL", "SPLUNK_MCP_TOKEN"} & _env_values(env_rendered).keys()
        assert not {"SPLUNK_MCP_URL", "SPLUNK_MCP_TOKEN"} & _env_values(profile_env).keys()
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
    # The owner under a Splunk-owning roster; a legacy identity under an empty roster.
    for identity, owner, owner_map in (("example-agent", True, ROSTER), ("hermes", False, {})):
        context = _config_context(identity, owner_map=owner_map)
        context["hermes_agent_profile"] = profile
        for template in ("config.yaml.j2", "config-profile.yaml.j2"):
            rendered = config_env.get_template(template).render(**context)
            assert bearer in rendered, (template, identity)
            assert (header in rendered) is owner, (template, identity)

        env_context = _env_owner_context(identity, owner_map=owner_map)
        env_rendered = env_env.get_template("hermes-env.j2").render(**env_context)
        line_after_token = env_rendered.split("SPLUNK_MCP_TOKEN=", 1)[1].split("\n", 2)[1]
        expected = f"HERMES_AGENT_GATEWAY_KEY={GATEWAY_KEY}" if owner else ""
        assert line_after_token == expected, identity


def test_empty_splunk_roster_keeps_legacy_splunk_render() -> None:
    env = _jinja_env()
    for identity in ("hermes", "hermes-private", "donna"):
        context = _config_context(identity, owner_map={})
        rendered = env.get_template("config.yaml.j2").render(**context)
        server = _mcp_servers(rendered)["splunk"]
        assert "X-Agent-Key" not in server["headers"]

        env_rendered = _env_jinja_env().get_template("hermes-env.j2").render(
            **_env_owner_context(identity, owner_map={})
        )
        assert _env_values(env_rendered)["SPLUNK_MCP_URL"] == "https://mcp.example.com/splunk"
        assert _env_values(env_rendered)["SPLUNK_MCP_TOKEN"] == SPLUNK_FALLBACK
