"""Hindsight banks are per agent x trust tier and derived from the agent roster.

An agent writes only its own bank, reads banks of agents in its own tier, and is
refused every cross-tier read. No agent or tier list is typed in the role: the
roster's explicit trust_tier field drives the bank id.
"""

from __future__ import annotations

import yaml
from cel_subset import authorize
from hindsight_acl_support import (
    AGENTS,
    API,
    BANKS,
    KEYS,
    TIERS,
    TRUSTED,
    TRUSTED_PEER,
    UNTRUSTED,
    UNTRUSTED_PEER,
    _allowed,
    _hindsight_context,
    _render_hindsight,
    _request,
)
from test_authz_render import REPO_ROOT, _routes

ROLE = REPO_ROOT / "roles/agentgateway_docker"


def _rules_for(agents: dict, keys: dict) -> list[dict[str, str]]:
    routes = _routes(_render_hindsight(agents=agents, keys=keys))
    route = next(r for r in routes if r["matches"][0]["path"].get("pathPrefix") == "/hindsight")
    return route["policies"]["authorization"]["rules"]


# --- the mapping rule ---------------------------------------------------------


def test_bank_is_agent_name_plus_tier_for_every_tiered_roster_agent() -> None:
    assert TIERS == {
        TRUSTED: "trusted",
        TRUSTED_PEER: "trusted",
        UNTRUSTED: "untrusted",
        UNTRUSTED_PEER: "untrusted",
    }
    assert BANKS == {agent: f"{agent}-{tier}" for agent, tier in TIERS.items()}
    assert len(set(BANKS.values())) == len(BANKS)
    # An agent with no trust_tier gets no bank, hence no access.
    assert "no-tier-agent" not in TIERS
    assert "no-tier-agent" not in BANKS


def test_new_roster_agent_gets_a_bank_and_a_tier_with_no_role_edit() -> None:
    agents = {**AGENTS, "research-scout": {"trust_tier": "trusted"}}
    keys = {**KEYS, "research_scout_agentgateway_key": "example-key-new"}
    context = _hindsight_context(agents=agents, keys=keys)
    new_bank = "research-scout-trusted"
    assert context["agentgateway_docker_hindsight_roster_tiers"]["research-scout"] == "trusted"
    assert context["agentgateway_docker_hindsight_agent_banks"]["research-scout"] == new_bank
    rules = _rules_for(agents, keys)
    assert _allowed(rules, "research-scout", "POST", f"{API}/{new_bank}/memories", tier="trusted")
    assert _allowed(rules, TRUSTED, "POST", f"{API}/{new_bank}/memories/recall")
    assert not _allowed(rules, UNTRUSTED, "POST", f"{API}/{new_bank}/memories/recall")


def test_retiering_a_roster_agent_moves_its_bank_across_the_boundary() -> None:
    agents = {**AGENTS, TRUSTED_PEER: {"trust_tier": "untrusted"}}
    rules = _rules_for(agents, KEYS)
    moved = f"{TRUSTED_PEER}-untrusted"
    assert _allowed(rules, TRUSTED_PEER, "POST", f"{API}/{moved}/memories", tier="untrusted")
    assert _allowed(rules, UNTRUSTED, "POST", f"{API}/{moved}/memories/recall")
    assert not _allowed(rules, TRUSTED, "POST", f"{API}/{moved}/memories/recall")
    # The old trusted bank id no longer exists for anyone.
    old = f"{TRUSTED_PEER}-trusted"
    assert not any(_allowed(rules, agent, "POST", f"{API}/{old}/memories/recall") for agent in TIERS)


def test_agent_without_a_gateway_key_gets_no_bank() -> None:
    keys = {k: v for k, v in KEYS.items() if k != "hermes_ops_agentgateway_key"}
    context = _hindsight_context(keys=keys)
    assert TRUSTED_PEER not in context["agentgateway_docker_hindsight_agent_banks"]


# --- the three policy facts ---------------------------------------------------


def test_own_bank_write_is_allowed_and_peer_bank_write_is_not(rules: list[dict[str, str]]) -> None:
    for agent in TIERS:
        own = BANKS[agent]
        assert _allowed(rules, agent, "POST", f"{API}/{own}/memories"), agent
        assert _allowed(rules, agent, "POST", f"{API}/{own}/files/retain"), agent
        for peer, peer_bank in BANKS.items():
            if peer != agent:
                assert not _allowed(rules, agent, "POST", f"{API}/{peer_bank}/memories"), (agent, peer)


def test_same_tier_read_is_allowed(rules: list[dict[str, str]]) -> None:
    for agent, tier in TIERS.items():
        for peer, peer_bank in BANKS.items():
            if TIERS[peer] == tier:
                assert _allowed(rules, agent, "POST", f"{API}/{peer_bank}/memories/recall"), (agent, peer)
                assert _allowed(rules, agent, "GET", f"{API}/{peer_bank}/memories/list"), (agent, peer)


def test_cross_tier_read_is_denied(rules: list[dict[str, str]]) -> None:
    for agent, tier in TIERS.items():
        for peer, peer_bank in BANKS.items():
            if TIERS[peer] != tier:
                for method, suffix in (("POST", "memories/recall"), ("GET", "memories/list"), ("POST", "reflect")):
                    assert not _allowed(rules, agent, method, f"{API}/{peer_bank}/{suffix}"), (agent, peer)


def test_a_forged_tier_claim_cannot_cross_tiers(rules: list[dict[str, str]]) -> None:
    # Tier is key metadata set by the gateway, never by the caller. A mismatched
    # agent/tier pair still cannot write a foreign bank: writes are owner-only.
    forged = authorize(rules, _request(UNTRUSTED, "POST", f"{API}/{BANKS[TRUSTED]}/memories", tier="trusted"))
    assert forged[0] is False


# --- no second hand-maintained list --------------------------------------------


def test_no_agent_or_tier_literal_list_in_the_role() -> None:
    defaults = ROLE / "defaults/main/20-hindsight-acl.yml"
    document = yaml.safe_load(defaults.read_text())
    for retired in ("banks", "tier_prefixes", "agent_tiers", "class_tiers", "tiers"):
        assert f"agentgateway_docker_hindsight_{retired}" not in document
    role_sources = [
        path.read_text()
        for directory in ("defaults", "tasks", "templates")
        for path in sorted((ROLE / directory).rglob("*"))
        if path.is_file()
    ]
    for source in role_sources:
        assert not any(name in source for name in AGENTS)
    for tier in ("trusted", "untrusted"):
        assert all(tier not in source for source in role_sources), tier
