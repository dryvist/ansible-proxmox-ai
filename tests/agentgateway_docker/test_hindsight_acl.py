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
    KEYS,
    SENSITIVE,
    SHARED,
    TENANT_KEY,
    _allowed,
    _hindsight_context,
    _render_hindsight,
    _request,
)
from test_authz_render import REPO_ROOT, _render, _routes



def test_role_renders_tiers_and_the_route_without_changing_other_routes() -> None:
    rendered = _render_hindsight()
    assert "tier: \"trusted\"" in rendered
    metadata = {
        key["metadata"]["agent"]: key["metadata"]
        for key in _routes(rendered)[-1]["policies"]["apiKey"]["keys"]
    }
    assert metadata["hermes-brain"] == {"agent": "hermes-brain", "tier": "trusted"}
    assert metadata["hermes-public"] == {"agent": "hermes-public", "tier": "trusted"}
    assert metadata["paperclip"] == {"agent": "paperclip", "tier": "untrusted"}
    assert metadata["no-tier-agent"] == {"agent": "no-tier-agent"}
    # Every pre-existing route is untouched apart from the new tier metadata.
    assert len(_routes(rendered)) == len(_routes(_render(AGENTS, {}, KEYS))) + 1


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


def test_route_is_omitted_without_the_tenant_key_or_the_roster() -> None:
    no_key = _routes(_render_hindsight(tenant_key=""))
    assert not [r for r in no_key if r["matches"][0]["path"].get("pathPrefix") == "/hindsight"]
    no_roster = _routes(_render({}, {}, {}, extra=_hindsight_context()))
    assert not [r for r in no_roster if r["matches"][0]["path"].get("pathPrefix") == "/hindsight"]


def test_bank_names_come_from_defaults_not_the_template() -> None:
    source = (REPO_ROOT / "roles/agentgateway_docker/templates/config.yaml.j2").read_text()
    assert f'"{SENSITIVE}"' not in source
    assert "/shared/" not in source
    custom = _hindsight_context()
    custom["agentgateway_docker_hindsight_banks"] = {"trusted_write": "vault_a", "shared": "common_b"}
    rendered = _render(AGENTS, {}, KEYS, extra=custom)
    assert "vault_a" in rendered and "common_b" in rendered
    assert SENSITIVE not in rendered.split("pathPrefix: /hindsight", 1)[1]


# --- positive cases ---------------------------------------------------------


POSITIVE = [
    # untrusted: read and write shared
    ("untrusted", "POST", f"{API}/{SHARED}/memories", {}),
    ("untrusted", "POST", f"{API}/{SHARED}/memories/recall", {}),
    ("untrusted", "POST", f"{API}/{SHARED}/reflect", {}),
    ("untrusted", "GET", f"{API}/{SHARED}/memories/list", {}),
    ("untrusted", "PATCH", f"{API}/{SHARED}/documents/a/b/c.md", {}),
    ("untrusted", "DELETE", f"{API}/{SHARED}/documents/doc1", {}),
    ("untrusted", "GET", f"{API}/{SHARED}/operations/abc", {}),
    ("untrusted", "POST", f"{API}/{SHARED}/mental-models/mm1/dry-run-refresh", {}),
    ("untrusted", "POST", f"{API}/{SHARED}/memories/recall", {"X-Bank-Id": SHARED}),
    ("untrusted", "POST", f"/hindsight/mcp/{SHARED}/", {}),
    ("untrusted", "GET", f"/hindsight/mcp/{SHARED}", {}),
    # trusted: read shared
    ("trusted", "GET", f"{API}/{SHARED}/memories/list", {}),
    ("trusted", "POST", f"{API}/{SHARED}/memories/recall", {}),
    ("trusted", "POST", f"{API}/{SHARED}/reflect", {}),
    ("trusted", "GET", f"{API}/{SHARED}/documents/x/y", {}),
    # trusted: read and write sensitive
    ("trusted", "GET", f"{API}/{SENSITIVE}/memories/list", {}),
    ("trusted", "POST", f"{API}/{SENSITIVE}/memories/recall", {}),
    ("trusted", "POST", f"{API}/{SENSITIVE}/memories", {}),
    ("trusted", "PATCH", f"{API}/{SENSITIVE}/documents/a/b", {}),
    ("trusted", "PUT", f"{API}/{SENSITIVE}/profile", {}),
    ("trusted", "DELETE", f"{API}/{SENSITIVE}/memories/m1/observations", {}),
    ("trusted", "POST", f"/hindsight/mcp/{SENSITIVE}/", {}),
    # either tier: public probes
    ("trusted", "GET", "/hindsight/health", {}),
    ("untrusted", "GET", "/hindsight/version", {}),
]


@pytest.mark.parametrize(("tier", "method", "path", "headers"), POSITIVE)
def test_positive_cases_are_allowed(rules: list[dict[str, str]], tier, method, path, headers) -> None:
    assert authorize(rules, _request(tier, method, path, headers))[0] is True


def test_every_allow_rule_is_exercised_by_a_positive_case(rules: list[dict[str, str]]) -> None:
    matched = {authorize(rules, _request(*case))[1] for case in POSITIVE}
    allow_indexes = {i for i, rule in enumerate(rules) if "allow" in rule}
    assert matched == allow_indexes


def test_rewrite_before_authorization_is_also_safe(rules: list[dict[str, str]]) -> None:
    # If the gateway rewrites the path before it evaluates rules, the prefix is already gone.
    assert _allowed(rules, "untrusted", "POST", f"/v1/default/banks/{SHARED}/memories")
    assert not _allowed(rules, "untrusted", "POST", f"/v1/default/banks/{SENSITIVE}/memories")
    assert not _allowed(rules, "trusted", "POST", f"/v1/default/banks/{SHARED}/memories")
