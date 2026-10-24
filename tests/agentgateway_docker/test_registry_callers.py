"""Registry harness callers get gateway keys and tier banks beside the roster agents.

`ai_callers` merges the released harness registry with the agent roster. A registry
caller therefore gets a key field, a tier and a bank with no roster entry, a roster
agent keeps its own tier, and an id owned by both sources fails the role assert.
"""

from __future__ import annotations

import jinja2
import yaml
from ansible.plugins.filter.mathstuff import FilterModule as MathFilters
from hindsight_acl_support import (
    API,
    KEYS,
    TRUSTED,
    UNTRUSTED,
    _allowed,
    _hindsight_context,
    _render_hindsight,
)
from test_authz_render import ENV, TASKS, _render_callers, _render_key_fields, _routes

REGISTRY = {
    "example-harness-trusted": {"trust_tier": "trusted"},
    "example-harness-untrusted": {"trust_tier": "untrusted"},
}
REGISTRY_KEYS = {
    "example_harness_trusted_agentgateway_key": "example-key-registry-trusted",
    "example_harness_untrusted_agentgateway_key": "example-key-registry-untrusted",
}
ROSTER = {"example-roster-agent": {"trust_tier": "untrusted"}}
ROSTER_KEYS = {"example_roster_agent_agentgateway_key": "example-key-roster"}


def test_registry_callers_and_roster_agents_merge_into_one_map() -> None:
    assert _render_callers(ROSTER, REGISTRY) == {**REGISTRY, "example-roster-agent": {"trust_tier": "untrusted"}}
    # No registry loaded: the roster alone, and nothing at all when both are empty.
    assert _render_callers(ROSTER) == {"example-roster-agent": {"trust_tier": "untrusted"}}
    assert _render_callers({}) == {}


def test_registry_caller_gets_a_key_field_a_tier_and_a_bank() -> None:
    assert _render_key_fields(ROSTER, REGISTRY) == {
        "example-harness-trusted": "example_harness_trusted_agentgateway_key",
        "example-harness-untrusted": "example_harness_untrusted_agentgateway_key",
        "example-roster-agent": "example_roster_agent_agentgateway_key",
    }
    keys = {**REGISTRY_KEYS, **ROSTER_KEYS}
    context = _hindsight_context(agents=ROSTER, keys=keys, registry=REGISTRY)
    assert context["agentgateway_docker_hindsight_roster_tiers"] == {
        "example-harness-trusted": "trusted",
        "example-harness-untrusted": "untrusted",
        "example-roster-agent": "untrusted",
    }
    assert context["agentgateway_docker_hindsight_agent_banks"] == {
        "example-harness-trusted": "example-harness-trusted-trusted",
        "example-harness-untrusted": "example-harness-untrusted-untrusted",
        "example-roster-agent": "example-roster-agent-untrusted",
    }


def test_registry_caller_without_a_key_gets_no_bank() -> None:
    context = _hindsight_context(agents=ROSTER, keys=ROSTER_KEYS, registry=REGISTRY)
    assert set(context["agentgateway_docker_hindsight_agent_banks"]) == {"example-roster-agent"}


def test_registry_caller_is_authorized_to_its_own_tier_only() -> None:
    keys = {**KEYS, **REGISTRY_KEYS}
    routes = _routes(_render_hindsight(keys=keys, registry=REGISTRY))
    route = next(
        r
        for r in routes
        if r["matches"][0]["path"].get("pathPrefix") == "/hindsight"
        and r["policies"]["apiKey"]["location"]["header"]["name"] == "X-Agent-Key"
    )
    rules = route["policies"]["authorization"]["rules"]
    own = "example-harness-trusted-trusted"
    assert _allowed(rules, "example-harness-trusted", "POST", f"{API}/{own}/memories", tier="trusted")
    # A roster agent of the same tier reads it; an untrusted caller does not.
    assert _allowed(rules, TRUSTED, "POST", f"{API}/{own}/memories/recall")
    assert not _allowed(rules, UNTRUSTED, "POST", f"{API}/{own}/memories/recall")
    assert not _allowed(rules, "example-harness-untrusted", "POST", f"{API}/{own}/memories/recall")


def test_roster_agent_keeps_its_own_tier_beside_the_registry() -> None:
    callers = _render_callers({"example-roster-agent": {"trust_tier": "trusted"}}, REGISTRY)
    assert callers["example-roster-agent"] == {"trust_tier": "trusted"}
    # A roster agent with no tier carries an empty one, which yields no bank.
    assert _render_callers({"example-roster-agent": {}}, REGISTRY)["example-roster-agent"] == {"trust_tier": ""}


def test_an_id_in_both_roster_and_registry_fails_the_role_assert() -> None:
    tasks = yaml.safe_load(TASKS.read_text())
    task = next(t for t in tasks if t.get("name", "").startswith("Validate that no caller id is in both"))
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters.update(ENV.filters)
    env.filters.update(MathFilters().filters())
    collisions_expression = task["vars"]["_caller_id_collisions"].strip().removeprefix("{{").removesuffix("}}")
    condition = task["ansible.builtin.assert"]["that"][0]

    def passes(roster: dict, registry: dict | None) -> bool:
        collisions = env.compile_expression(collisions_expression.strip())(
            ai_agents=roster, agent_callers_registry=registry or {}
        )
        return env.compile_expression(condition)(_caller_id_collisions=collisions)

    assert passes(ROSTER, REGISTRY) is True
    assert passes(ROSTER, None) is True
    assert passes({"example-harness-trusted": {"trust_tier": "trusted"}}, REGISTRY) is False
