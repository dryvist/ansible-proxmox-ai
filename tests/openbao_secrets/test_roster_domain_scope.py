"""Roster domains are fetched only by the plays whose hosts read them."""

from pathlib import Path

import yaml
from jinja2.nativetypes import NativeEnvironment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROSTER_DEFAULTS = REPO_ROOT / "inventory/group_vars/all/openbao_secrets/11-roster-domains.yml"
SITE = REPO_ROOT / "playbooks/site.yml"
ROSTER = {"hermes-x": {"container": "hermes-x", "owns": []}, "ci-fixer": {"owns": []}}
HOSTVARS = {
    "hermes-x": {"group_names": ["hermes_agent_group", "managed_secrets_group"]},
    "llm-router-1": {"group_names": ["llm_router_group", "managed_secrets_group"]},
}


def _environment():
    environment = NativeEnvironment()
    environment.filters["extract"] = lambda key, container, attribute: container[key][attribute]
    environment.filters["flatten"] = lambda lists: [item for sub in lists for item in sub]
    environment.filters["unique"] = lambda items: list(dict.fromkeys(items))
    return environment


def _domain_groups(ai_agents):
    roster = yaml.safe_load(ROSTER_DEFAULTS.read_text(encoding="utf-8"))
    return _environment().from_string(roster["openbao_secrets_domain_groups"]).render(ai_agents=ai_agents)


def _active_groups(play_hosts):
    prefetch = next(play for play in yaml.safe_load(SITE.read_text(encoding="utf-8"))
                    if play.get("name") == "Pre-fetch resource-domain secrets from OpenBao")
    fetch = next(task for task in prefetch["tasks"]
                 if task.get("ansible.builtin.include_role", {}).get("name") == "dryvist.homelab.openbao_secrets")
    expression = fetch["vars"]["openbao_secrets_active_groups"]
    return _environment().from_string(expression).render(ansible_play_hosts_all=play_hosts, hostvars=HOSTVARS)


def _selected(domain_names, domain_groups, active_groups):
    # dryvist.homelab.openbao_secrets narrow_domains.yml: a domain with consumer
    # groups is kept only when one is active; a domain with none is always kept.
    return [name for name in domain_names
            if not domain_groups.get(name) or set(domain_groups[name]) & set(active_groups)]


def test_hermes_agent_only_play_resolves_without_container_less_roster_domains():
    selected = _selected(["apps", *ROSTER], _domain_groups(ROSTER), _active_groups(["hermes-x"]))

    assert "hermes-x" in selected
    assert "apps" in selected
    assert "ci-fixer" not in selected


def test_router_play_still_requires_every_roster_domain():
    selected = _selected(list(ROSTER), _domain_groups(ROSTER), _active_groups(["llm-router-1"]))

    assert "ci-fixer" in selected
    assert "hermes-x" in selected


def test_only_roster_domains_are_group_scoped():
    groups = _domain_groups(ROSTER)

    assert "llm_router_group" in groups["ci-fixer"]
    assert "hermes_agent_group" not in groups["ci-fixer"]
    assert "hermes_agent_group" in groups["hermes-x"]
    assert "apps" not in groups
    assert _domain_groups({}) == {}
