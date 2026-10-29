"""Roster-backed OpenBao domains preserve the static and identity views."""

import re
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment


REPO_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_DEFAULTS = REPO_ROOT / "inventory/group_vars/all/openbao_secrets/10-domains.yml"
ROSTER_DEFAULTS = REPO_ROOT / "inventory/group_vars/all/openbao_secrets/11-roster-domains.yml"
IDENTITY_DEFAULTS = REPO_ROOT / "inventory/group_vars/all/openbao_secrets/10-domains-identities.yml"
AGENT_DEFAULTS = REPO_ROOT / "inventory/group_vars/hermes_agent_group/00-agent-settings.yml"
ROSTER = {"splunk-heimdall": {"owns": ["splunk"]}}
TWO_AGENT_ROSTER = {"a-x": {"owns": []}, "b-y": {"owns": []}}
READER_DOMAIN_NAMES = {"llm-router-agent-keys", "agentgateway-agent-keys"}


def _static_domains() -> list:
    """The static domain list: the fleet part, then the identity part (10-domains.yml concatenates them)."""
    fleet = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))["_openbao_secrets_static_domains_fleet"]
    identities = yaml.safe_load(IDENTITY_DEFAULTS.read_text(encoding="utf-8"))[
        "_openbao_secrets_static_domains_identities"
    ]
    return fleet + identities


def _render_domains(ai_agents, registry=None):
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    roster = yaml.safe_load(ROSTER_DEFAULTS.read_text(encoding="utf-8"))
    owners = {
        slug: agent_name
        for agent_name, agent in ai_agents.items()
        for slug in agent.get("owns", [])
    }
    return NativeEnvironment().from_string(roster["openbao_secrets_domains"]).render(
        _openbao_secrets_static_domains=_static_domains(),
        ai_agents=ai_agents,
        ai_agent_owner=owners,
        ai_callers={**(registry or {}), **{name: {} for name in ai_agents}},
    )


def _render_optional_domains(ai_agents, registry=None):
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    roster = yaml.safe_load(ROSTER_DEFAULTS.read_text(encoding="utf-8"))
    return NativeEnvironment().from_string(roster["openbao_secrets_optional_domains"]).render(
        _openbao_secrets_static_optional_domains=defaults[
            "_openbao_secrets_static_optional_domains"
        ],
        ai_agents=ai_agents,
        ai_callers={**(registry or {}), **{name: {} for name in ai_agents}},
    )


def test_empty_roster_keeps_static_domain_lists_unchanged():
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    domains = _render_domains({})

    assert domains == _static_domains()
    assert READER_DOMAIN_NAMES.isdisjoint({domain["name"] for domain in domains})
    assert _render_optional_domains({}) == defaults["_openbao_secrets_static_optional_domains"]


def test_roster_agent_gets_its_paths_and_owned_mcp_paths():
    domains = _render_domains(ROSTER)

    assert [domain for domain in domains if domain["name"] == "splunk-heimdall"] == [
        {
            "name": "splunk-heimdall",
            "paths": [
                "ai/splunk-heimdall",
                "apps/splunk-heimdall",
                {"path": "apps/splunk-heimdall-gw", "optional": True},
                "ai/mcp/splunk",
            ],
            "required": True,
        }
    ]


def test_owned_mcp_paths_are_removed_from_legacy_domains():
    domains = _render_domains(ROSTER)
    legacy_domains = [domain for domain in domains if domain["name"] != "splunk-heimdall"]

    assert all("ai/mcp/splunk" not in domain["paths"] for domain in legacy_domains)


def test_registry_callers_get_a_gateway_leaf_but_no_domain_of_their_own():
    registry = {"example-harness": {"trust_tier": "trusted"}}
    domains = _render_domains(TWO_AGENT_ROSTER, registry)
    by_name = {domain["name"]: domain for domain in domains}

    # Every caller leaf is optional at fetch; the gateway role separately
    # asserts that roster-agent keys exist before it deploys.
    assert by_name["agentgateway-agent-keys"]["paths"] == [
        {"path": "apps/example-harness-gw", "optional": True},
        {"path": "apps/a-x-gw", "optional": True},
        {"path": "apps/b-y-gw", "optional": True},
    ]
    assert by_name["llm-router-agent-keys"]["paths"] == ["apps/a-x", "apps/b-y"]
    assert "example-harness" not in by_name


def test_registry_callers_alone_still_get_the_gateway_reader_domain():
    registry = {"example-harness": {"trust_tier": "trusted"}}
    by_name = {domain["name"]: domain for domain in _render_domains({}, registry)}

    assert by_name["agentgateway-agent-keys"]["paths"] == [
        {"path": "apps/example-harness-gw", "optional": True}
    ]
    assert "llm-router-agent-keys" not in by_name
    assert _render_optional_domains({}, registry) == [
        *_render_optional_domains({}),
        "agentgateway-agent-keys",
    ]


def test_domain_names_map_to_distinct_approle_env_prefixes():
    for domains in (_static_domains(), _render_domains(ROSTER), _render_domains(TWO_AGENT_ROSTER)):
        env_prefixes = [
            re.sub(r"[^A-Z0-9]", "_", domain["name"].upper())
            for domain in domains
        ]
        assert len(env_prefixes) == len(set(env_prefixes))



def test_reader_domains_cover_each_roster_key_family_and_are_optional():
    domains = _render_domains(TWO_AGENT_ROSTER)
    by_name = {domain["name"]: domain for domain in domains}

    assert by_name["llm-router-agent-keys"] == {
        "name": "llm-router-agent-keys",
        "paths": ["apps/a-x", "apps/b-y"],
    }
    assert by_name["agentgateway-agent-keys"] == {
        "name": "agentgateway-agent-keys",
        "paths": [
            {"path": "apps/a-x-gw", "optional": True},
            {"path": "apps/b-y-gw", "optional": True},
        ],
    }

    optional_domains = _render_optional_domains(TWO_AGENT_ROSTER)
    assert optional_domains == [
        *_render_optional_domains({}),
        "llm-router-agent-keys",
        "agentgateway-agent-keys",
    ]
    assert READER_DOMAIN_NAMES <= set(optional_domains)
    assert {"a-x", "b-y"}.isdisjoint(optional_domains)


def test_roster_domain_is_selected_by_the_container_identity():
    domains = _render_domains(ROSTER)
    domain_values = {domain["name"]: {"selected": domain["name"]} for domain in domains}
    agent_vars = yaml.safe_load(AGENT_DEFAULTS.read_text(encoding="utf-8"))
    environment = NativeEnvironment()
    environment.filters["combine"] = lambda left, right: {**left, **right}
    selected = environment.from_string(agent_vars["hermes_agent_bao"]).render(
        hermes_agent_identity="splunk-heimdall",
        hermes_agent_bao_local_llm_layer=True,
        bao_local_llm_secrets={"unrelated": "local-llm"},
        openbao_secrets_by_domain=domain_values,
    )

    assert selected == {"selected": "splunk-heimdall"}


def test_each_hermes_identity_reads_only_its_own_optional_gateway_leaf():
    identities = {"hermes": {"owns": []}, "hermes-private": {"owns": []}}
    by_name = {domain["name"]: domain for domain in _render_domains(identities)}

    for identity, other in (("hermes", "hermes-private"), ("hermes-private", "hermes")):
        domain = by_name[identity]
        assert {"path": f"apps/{identity}-gw", "optional": True} in domain["paths"]
        assert f"apps/{other}-gw" not in domain["paths"]
