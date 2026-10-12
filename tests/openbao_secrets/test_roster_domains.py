"""Roster-backed OpenBao domains preserve the static and identity views."""

import re
from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment


REPO_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_DEFAULTS = REPO_ROOT / "roles/openbao_secrets/defaults/main/10-domains.yml"
ROSTER_DEFAULTS = REPO_ROOT / "roles/openbao_secrets/defaults/main/11-roster-domains.yml"
AGENT_DEFAULTS = REPO_ROOT / "inventory/group_vars/hermes_agent_group/00-agent-settings.yml"
FETCH_DOMAIN_TASKS = REPO_ROOT / "roles/openbao_secrets/tasks/fetch_domain.yml"
ROLE_TASKS = REPO_ROOT / "roles/openbao_secrets/tasks/main.yml"
ROSTER = {"splunk-heimdall": {"owns": ["splunk"]}}
TWO_AGENT_ROSTER = {"a-x": {"owns": []}, "b-y": {"owns": []}}
READER_DOMAIN_NAMES = {"llm-router-agent-keys", "agentgateway-agent-keys"}


def _render_domains(ai_agents):
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    roster = yaml.safe_load(ROSTER_DEFAULTS.read_text(encoding="utf-8"))
    owners = {
        slug: agent_name
        for agent_name, agent in ai_agents.items()
        for slug in agent.get("owns", [])
    }
    return NativeEnvironment().from_string(roster["openbao_secrets_domains"]).render(
        _openbao_secrets_static_domains=defaults["_openbao_secrets_static_domains"],
        ai_agents=ai_agents,
        ai_agent_owner=owners,
    )


def _render_optional_domains(ai_agents):
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    roster = yaml.safe_load(ROSTER_DEFAULTS.read_text(encoding="utf-8"))
    return NativeEnvironment().from_string(roster["openbao_secrets_optional_domains"]).render(
        _openbao_secrets_static_optional_domains=defaults[
            "_openbao_secrets_static_optional_domains"
        ],
        ai_agents=ai_agents,
    )


def test_empty_roster_keeps_static_domain_lists_unchanged():
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    domains = _render_domains({})

    assert domains == defaults["_openbao_secrets_static_domains"]
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
                "apps/splunk-heimdall-gw",
                "ai/mcp/splunk",
            ],
            "required": True,
        }
    ]


def test_owned_mcp_paths_are_removed_from_legacy_domains():
    domains = _render_domains(ROSTER)
    legacy_domains = [domain for domain in domains if domain["name"] != "splunk-heimdall"]

    assert all("ai/mcp/splunk" not in domain["paths"] for domain in legacy_domains)


def test_optional_fetch_skip_does_not_apply_to_required_domains():
    tasks = yaml.safe_load(FETCH_DOMAIN_TASKS.read_text(encoding="utf-8"))
    skip_task = next(
        task for task in tasks if task["name"].startswith("Report a deliberately optional")
    )
    fail_task = next(
        task for task in tasks if task["name"].startswith("Fail on missing credentials")
    )

    assert any(
        "not (openbao_domain.required | default(false))" in condition
        for condition in skip_task["when"]
    )
    assert any(
        "or (openbao_domain.required | default(false))" in condition
        for condition in fail_task["when"]
    )


def test_domain_names_map_to_distinct_approle_env_prefixes():
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    tasks = yaml.safe_load(ROLE_TASKS.read_text(encoding="utf-8"))
    assertion = next(
        task["ansible.builtin.assert"]
        for task in tasks
        if "ansible.builtin.assert" in task
    )
    env = NativeEnvironment()
    env.filters["regex_replace"] = lambda value, pattern, repl: re.sub(pattern, repl, value)
    distinct = env.from_string("{{ " + assertion["that"][0] + " }}")

    assert distinct.render(openbao_secrets_domains=defaults["_openbao_secrets_static_domains"])
    assert distinct.render(openbao_secrets_domains=_render_domains(ROSTER))
    for colliding in (
        {"hermes-private": {}},
        {"hermes_private": {}},
        {"llm-router-agent-keys": {}},
        {"llm_router_agent_keys": {}},
        {"agentgateway-agent-keys": {}},
        {"a-x": {}, "a_x": {}},
    ):
        assert distinct.render(openbao_secrets_domains=_render_domains(colliding)) is False, colliding



def test_reader_domains_cover_each_roster_key_family_and_are_optional():
    domains = _render_domains(TWO_AGENT_ROSTER)
    by_name = {domain["name"]: domain for domain in domains}

    assert by_name["llm-router-agent-keys"] == {
        "name": "llm-router-agent-keys",
        "paths": ["apps/a-x", "apps/b-y"],
    }
    assert by_name["agentgateway-agent-keys"] == {
        "name": "agentgateway-agent-keys",
        "paths": ["apps/a-x-gw", "apps/b-y-gw"],
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
