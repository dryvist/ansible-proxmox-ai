"""The /hindsight route enforces the bank ACL: allow-list rules, per-caller tier.

The rendered CEL rules are executed by a small evaluator (cel_subset.py) against
sample requests. The evaluator models only the CEL subset the rules use and
raises on anything else, so it cannot silently agree with a rule it misreads.
It does not prove the gateway's own CEL engine, path handling or rewrite order;
those are checked by the converge smoke tests listed in the change report.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from cel_subset import authorize
from hindsight_acl_support import (
    AGENTS,
    API,
    BANKS,
    KEYS,
    TENANT_KEY,
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
from test_authz_render import _render, _routes


def test_role_renders_tiers_and_the_route_without_changing_other_routes() -> None:
    rendered = _render_hindsight()
    metadata = {
        key["metadata"]["agent"]: key["metadata"]
        for key in _routes(rendered)[-1]["policies"]["apiKey"]["keys"]
    }
    assert metadata[TRUSTED] == {"agent": TRUSTED, "tier": "trusted"}
    assert metadata[TRUSTED_PEER] == {"agent": TRUSTED_PEER, "tier": "trusted"}
    assert metadata[UNTRUSTED] == {"agent": UNTRUSTED, "tier": "untrusted"}
    assert metadata[UNTRUSTED_PEER] == {"agent": UNTRUSTED_PEER, "tier": "untrusted"}
    assert metadata["no-tier-agent"] == {"agent": "no-tier-agent"}
    # Existing routes only gain tier metadata; the Hindsight route is emitted twice for auth modes.
    assert len(_routes(rendered)) == len(_routes(_render(AGENTS, {}, KEYS))) + 2


def test_route_authenticates_strictly_and_injects_the_tenant_key(route: dict[str, Any]) -> None:
    policies = route["policies"]
    assert policies["apiKey"]["mode"] == "strict"
    assert policies["apiKey"]["location"] == {"header": {"name": "X-Agent-Key"}}
    assert policies["urlRewrite"] == {"authority": "auto", "path": {"prefix": "/"}}
    backend = route["backends"][0]
    assert backend["host"] == "hindsight.example.com:443"
    assert backend["policies"]["backendTLS"] == {"hostname": "hindsight.example.com"}
    assert backend["policies"]["backendAuth"] == {"key": {"value": TENANT_KEY}}
    # Callers never hold the tenant key: it appears only in the backendAuth value.
    assert TENANT_KEY not in yaml.safe_dump(policies["authorization"])
    assert TENANT_KEY not in yaml.safe_dump(policies["apiKey"])


def test_rules_are_allow_and_require_only(rules: list[dict[str, str]]) -> None:
    assert all(set(rule) in ({"allow"}, {"require"}) for rule in rules)
    assert [list(rule)[0] for rule in rules].count("require") == 1
    assert len(rules) >= 3


def test_route_is_omitted_without_the_tenant_key_or_an_agent_with_a_trust_tier() -> None:
    no_key = _routes(_render_hindsight(tenant_key=""))
    assert not [r for r in no_key if r["matches"][0]["path"].get("pathPrefix") == "/hindsight"]
    no_roster = _routes(_render({}, {}, {}, extra=_hindsight_context(agents={}, keys={})))
    assert not [r for r in no_roster if r["matches"][0]["path"].get("pathPrefix") == "/hindsight"]
    tierless = {"only-agent": {}}
    tierless_keys = {"only_agent_agentgateway_key": "example-key"}
    none_tiered = _routes(_render_hindsight(agents=tierless, keys=tierless_keys))
    assert not [r for r in none_tiered if r["matches"][0]["path"].get("pathPrefix") == "/hindsight"]


def test_service_containment_omits_only_the_hindsight_route() -> None:
    routes = _routes(_render_hindsight(service_enabled=False))
    paths = {
        route["matches"][0]["path"].get("pathPrefix")
        or route["matches"][0]["path"].get("exact")
        for route in routes
    }
    assert not any(path and path.startswith("/hindsight") for path in paths)
    assert "/splunk" in paths


# --- positive cases ---------------------------------------------------------


def _positive() -> list[tuple[str, str, str, dict[str, str]]]:
    own, peer, other = BANKS[TRUSTED], BANKS[TRUSTED_PEER], BANKS[UNTRUSTED]
    return [
        # own bank: read and write, REST and the single-bank MCP mount
        (TRUSTED, "POST", f"{API}/{own}/memories", {}),
        (TRUSTED, "POST", f"{API}/{own}/memories/recall", {}),
        (TRUSTED, "POST", f"{API}/{own}/reflect", {}),
        (TRUSTED, "GET", f"{API}/{own}/memories/list", {}),
        (TRUSTED, "PATCH", f"{API}/{own}/documents/a/b/c.md", {}),
        (TRUSTED, "DELETE", f"{API}/{own}/documents/doc1", {}),
        (TRUSTED, "PUT", f"{API}/{own}/profile", {}),
        (TRUSTED, "POST", f"{API}/{own}/mental-models/mm1/dry-run-refresh", {}),
        (TRUSTED, "POST", f"{API}/{own}/memories/recall", {"X-Bank-Id": own}),
        (TRUSTED, "POST", f"/hindsight/mcp/{own}/", {}),
        (TRUSTED, "GET", f"/hindsight/mcp/{own}", {}),
        (UNTRUSTED, "POST", f"{API}/{other}/memories", {}),
        (UNTRUSTED, "POST", f"/hindsight/mcp/{other}/", {}),
        # same-tier peer bank: read only
        (TRUSTED, "GET", f"{API}/{peer}/memories/list", {}),
        (TRUSTED, "POST", f"{API}/{peer}/memories/recall", {}),
        (TRUSTED, "POST", f"{API}/{peer}/reflect", {}),
        (TRUSTED, "GET", f"{API}/{peer}/documents/x/y", {}),
        (TRUSTED_PEER, "POST", f"{API}/{own}/memories/recall", {}),
        (UNTRUSTED, "POST", f"{API}/{BANKS[UNTRUSTED_PEER]}/memories/recall", {}),
        # either tier: public probes
        (TRUSTED, "GET", "/hindsight/health", {}),
        (UNTRUSTED, "GET", "/hindsight/version", {}),
    ]


@pytest.mark.parametrize(("agent", "method", "path", "headers"), _positive())
def test_positive_cases_are_allowed(rules: list[dict[str, str]], agent, method, path, headers) -> None:
    assert authorize(rules, _request(agent, method, path, headers))[0] is True


def test_every_allow_rule_is_exercised_by_a_positive_case(rules: list[dict[str, str]]) -> None:
    # Positives above cover trusted and untrusted owners; extend to every roster agent.
    cases = list(_positive())
    for agent, bank in BANKS.items():
        cases += [
            (agent, "POST", f"{API}/{bank}/memories", {}),
            (agent, "GET", f"{API}/{bank}/memories/list", {}),
            (agent, "POST", f"/hindsight/mcp/{bank}/", {}),
        ]
    matched = {authorize(rules, _request(*case))[1] for case in cases}
    allow_indexes = {i for i, rule in enumerate(rules) if "allow" in rule}
    assert matched == allow_indexes


def test_rewrite_before_authorization_is_also_safe(rules: list[dict[str, str]]) -> None:
    # If the gateway rewrites the path before it evaluates rules, the prefix is already gone.
    assert _allowed(rules, TRUSTED, "POST", f"/v1/default/banks/{BANKS[TRUSTED]}/memories")
    assert not _allowed(rules, TRUSTED, "POST", f"/v1/default/banks/{BANKS[UNTRUSTED]}/memories")
    assert not _allowed(rules, UNTRUSTED, "POST", f"/v1/default/banks/{BANKS[TRUSTED]}/memories")
