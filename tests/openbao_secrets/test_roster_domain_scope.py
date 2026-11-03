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
    "clickhouse": {"group_names": ["clickhouse_group", "managed_secrets_group"]},
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


def _domain_names(ai_agents):
    roster = yaml.safe_load(ROSTER_DEFAULTS.read_text(encoding="utf-8"))
    domains = _environment().from_string(roster["openbao_secrets_domains"]).render(
        _openbao_secrets_static_domains=[],
        ai_agents=ai_agents,
        ai_agent_owner={},
        ai_callers={name: {} for name in ai_agents},
    )
    return [domain["name"] for domain in domains]


def test_router_play_requires_guest_backed_and_owning_roster_domains_only():
    roster = {**ROSTER, "mcp-owner": {"owns": ["github"]}}
    selected = _selected(_domain_names(roster), _domain_groups(roster), _active_groups(["llm-router-1"]))

    assert "hermes-x" in selected
    assert "mcp-owner" in selected
    # An empty-owns, container-less agent's leaf rides the reader domain; it needs no login of its own.
    assert "ci-fixer" not in selected
    assert "llm-router-agent-keys" in selected


def test_only_roster_domains_are_group_scoped():
    groups = _domain_groups(ROSTER)

    assert "llm_router_group" in groups["ci-fixer"]
    assert "hermes_agent_group" not in groups["ci-fixer"]
    assert "hermes_agent_group" in groups["hermes-x"]
    assert "apps" not in groups
    assert _domain_groups({}) == {"clickhouse-wall": ["clickhouse_group"]}


def test_clickhouse_wall_domain_is_optional_until_its_approle_is_applied():
    static_optional = yaml.safe_load(
        (REPO_ROOT / "inventory/group_vars/all/openbao_secrets/10-domains.yml").read_text(encoding="utf-8")
    )["_openbao_secrets_static_optional_domains"]

    assert "clickhouse-wall" in static_optional


def test_clickhouse_wall_domain_is_fetched_only_by_the_clickhouse_play():
    names = ["apps", "clickhouse", "clickhouse-wall"]
    groups = _domain_groups(ROSTER)

    router_play = _selected(names, groups, _active_groups(["llm-router-1"]))
    clickhouse_play = _selected(names, groups, _active_groups(["clickhouse"]))

    assert "clickhouse-wall" not in router_play
    assert "clickhouse" in router_play
    assert "clickhouse-wall" in clickhouse_play
