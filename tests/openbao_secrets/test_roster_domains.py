"""Roster-backed OpenBao domains preserve the static and identity views."""

from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment


REPO_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_DEFAULTS = REPO_ROOT / "roles/openbao_secrets/defaults/main/10-domains.yml"
ROSTER_DEFAULTS = REPO_ROOT / "roles/openbao_secrets/defaults/main/11-roster-domains.yml"
AGENT_DEFAULTS = REPO_ROOT / "inventory/group_vars/hermes_agent_group/00-agent-settings.yml"
ROSTER = {"splunk-heimdall": {"owns": ["splunk"]}}


def _render_domains(ai_agents):
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))
    roster = yaml.safe_load(ROSTER_DEFAULTS.read_text(encoding="utf-8"))
    return NativeEnvironment().from_string(roster["openbao_secrets_domains"]).render(
        _openbao_secrets_static_domains=defaults["_openbao_secrets_static_domains"],
        ai_agents=ai_agents,
    )


def test_empty_roster_keeps_the_static_domain_list_unchanged():
    defaults = yaml.safe_load(DOMAIN_DEFAULTS.read_text(encoding="utf-8"))

    assert _render_domains({}) == defaults["_openbao_secrets_static_domains"]


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
        }
    ]


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
