"""A hermes_crons-only run fetches only the domains its cron reconcile reads.

Every other tag selection, skip selection, or play that includes a non-Hermes host keeps
the full domain list.
"""

from pathlib import Path

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "inventory/group_vars/all/openbao_secrets"
SITE = ROOT / "playbooks/site.yml"
ROSTER = yaml.safe_load((DIRECTORY / "11-roster-domains.yml").read_text(encoding="utf-8"))
IDENTITIES = yaml.safe_load((DIRECTORY / "10-domains-identities.yml").read_text(encoding="utf-8"))
FLEET = yaml.safe_load((DIRECTORY / "10-domains.yml").read_text(encoding="utf-8"))
SEED = yaml.safe_load((DIRECTORY / "00-seed-vars.yml").read_text(encoding="utf-8"))
AGENTS = {"hermes-x": {"container": "hermes-x", "owns": []}, "mcp-owner": {"owns": ["github"]}}
HERMES = "hermes_agent_group"
NOT_READ_BY_CRONS = [
    "apps", "ai-public", "ai-runner", "clickhouse", "clickhouse-wall", "llm-router-agent-keys",
    "agentgateway-agent-keys", "app-agent-vikunja-read", "app-agent-vikunja-write",
    "app-agent-zammad-write", "mcp-owner",
]


def _env():
    env = NativeEnvironment(undefined=StrictUndefined)
    env.filters.update(difference=lambda a, b: [x for x in a if x not in set(b)], bool=bool)
    return env


def _render(name, context):
    return _env().from_string(ROSTER[name]).render(context)


def _domains(play, run_tags, skip_tags=(), pepper_selected=False):
    """Play is a list of (host, hermes identity or None, group). Returns the domain names."""
    hostvars = {host: ({"hermes_agent_identity": identity} if identity else {})
                for host, identity, _ in play}
    groups = {}
    for host, _, group in play:
        groups.setdefault(group, []).append(host)
    context = dict(
        _openbao_secrets_static_domains=(FLEET["_openbao_secrets_static_domains_fleet"]
                                         + IDENTITIES["_openbao_secrets_static_domains_identities"]),
        openbao_app_agent_tier_reader_domains=IDENTITIES["openbao_app_agent_tier_reader_domains"],
        openbao_agent_tier_mount=SEED["openbao_agent_tier_mount"],
        openbao_secrets_pepper_paths=[],
        hermes_agent_pepper_runtime_selected=pepper_selected,
        ai_agents=AGENTS, ai_agent_owner={"github": "mcp-owner"},
        ai_callers={name: {} for name in AGENTS},
        ansible_play_hosts_all=[host for host, _, _ in play], hostvars=hostvars, groups=groups,
        ansible_run_tags=list(run_tags), ansible_skip_tags=list(skip_tags),
    )
    context["openbao_secrets_hermes_crons_only"] = _render("openbao_secrets_hermes_crons_only", context)
    return [domain["name"] for domain in _render("openbao_secrets_domains", context)]


def _hermes(identity):
    return (identity, identity, HERMES)


CRONS = ["hermes_crons"]


@pytest.mark.parametrize("play,kept", [
    ([_hermes("hermes-x")], {"hermes-x", "local-llm"}),
    ([_hermes("hermes")], {"hermes", "local-llm"}),
    ([_hermes("hermes-private")], {"hermes-private", "local-llm"}),
    ([_hermes("donna")], {"donna", "local-llm"}),
    ([_hermes("hermes"), _hermes("donna")], {"hermes", "donna", "local-llm"}),
])
def test_crons_only_run_keeps_each_host_identity_domain_and_local_llm(play, kept):
    domains = _domains(play, CRONS)

    assert set(domains) == kept
    assert not set(NOT_READ_BY_CRONS) & set(domains)


def test_crons_only_run_keeps_the_pepper_domain_only_when_its_runtime_is_selected():
    assert set(_domains([_hermes("pepper")], CRONS, pepper_selected=True)) == {"pepper", "local-llm"}


@pytest.mark.parametrize("run_tags,skip_tags", [
    (["all"], []),
    (["hermes_agent"], []),
    (["hermes_profiles_env"], []),
    (["hermes_crons", "hermes_units_gateways"], []),
    (["hermes_crons"], ["hermes_patches"]),
])
def test_other_selections_keep_every_domain(run_tags, skip_tags):
    play = [_hermes("hermes-x")]
    full = _domains(play, ["all"])

    assert set(NOT_READ_BY_CRONS) <= set(full)
    assert _domains(play, run_tags, skip_tags) == full


def test_a_play_with_a_non_hermes_host_keeps_every_domain():
    play = [_hermes("hermes-x"), ("llm-router-1", None, "llm_router_group")]

    assert _domains(play, CRONS) == _domains(play, ["all"])
    assert set(NOT_READ_BY_CRONS) <= set(_domains(play, CRONS))


def test_site_skips_the_caller_registry_for_a_crons_only_run():
    site = yaml.safe_load(SITE.read_text(encoding="utf-8"))
    prefetch = next(play for play in site if play.get("name") == "Pre-fetch resource-domain secrets from OpenBao")
    task = next(item for item in prefetch["pre_tasks"] if item.get("name") == "Load the harness caller registry")

    assert task["when"] == "not (openbao_secrets_hermes_crons_only | default(false) | bool)"
