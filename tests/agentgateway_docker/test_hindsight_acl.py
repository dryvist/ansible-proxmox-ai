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
from cel_subset import CelError, authorize, evaluate
from test_authz_render import (
    INVENTORY_VARS,
    REPO_ROOT,
    ROLE_DEFAULTS,
    _render,
    _render_map,
    _routes,
)

HINDSIGHT_DEFAULTS = yaml.safe_load(
    (REPO_ROOT / "roles/agentgateway_docker/defaults/main/20-hindsight-acl.yml").read_text()
)
AGENTS = {"hermes-brain": {}, "hermes-public": {}, "paperclip": {}, "no-tier-agent": {}}
KEYS = {
    "hermes_brain_agentgateway_key": "example-key-trusted",
    "hermes_public_agentgateway_key": "example-key-trusted-two",
    "paperclip_agentgateway_key": "example-key-untrusted",
    "no_tier_agent_agentgateway_key": "example-key-no-tier",
}
TENANT_KEY = "example-hindsight-tenant-key"
SHARED = HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_banks"]["shared"]
SENSITIVE = HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_banks"]["trusted_write"]
API = "/hindsight/v1/default/banks"


def _hindsight_context(tenant_key: str = TENANT_KEY, agent_tiers: dict[str, str] | None = None) -> dict[str, Any]:
    key_fields = _render_map(INVENTORY_VARS["ai_agent_gateway_key_fields"], ai_agents=AGENTS)
    roster_keys = _render_map(
        ROLE_DEFAULTS["agentgateway_docker_roster_keys"],
        ai_agent_gateway_key_fields=key_fields,
        openbao_secrets_by_domain={"agentgateway-agent-keys": KEYS},
    )
    context: dict[str, Any] = {
        name: value
        for name, value in HINDSIGHT_DEFAULTS.items()
        if name
        not in (
            "agentgateway_docker_hindsight_host",
            "agentgateway_docker_hindsight_api_key",
            "agentgateway_docker_hindsight_roster_tiers",
        )
    }
    context["agentgateway_docker_hindsight_host"] = "hindsight.example.com"
    context["agentgateway_docker_hindsight_api_key"] = tenant_key
    context["agentgateway_docker_hindsight_agent_tiers"] = agent_tiers or {"paperclip": "untrusted"}
    context["agentgateway_docker_hindsight_roster_tiers"] = _render_map(
        HINDSIGHT_DEFAULTS["agentgateway_docker_hindsight_roster_tiers"],
        agentgateway_docker_roster_keys=roster_keys,
        agentgateway_docker_hindsight_tier_prefixes=context["agentgateway_docker_hindsight_tier_prefixes"],
        agentgateway_docker_hindsight_agent_tiers=context["agentgateway_docker_hindsight_agent_tiers"],
    )
    return context


def _render_hindsight(**kwargs: Any) -> str:
    return _render(AGENTS, {}, KEYS, extra=_hindsight_context(**kwargs))


@pytest.fixture(scope="module")
def route() -> dict[str, Any]:
    routes = _routes(_render_hindsight())
    return next(r for r in routes if r["matches"][0]["path"].get("pathPrefix") == "/hindsight")


@pytest.fixture(scope="module")
def rules(route: dict[str, Any]) -> list[dict[str, str]]:
    return route["policies"]["authorization"]["rules"]


def _request(
    tier: str | None, method: str, path: str, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "request": {"method": method, "path": path, "headers": {k.lower(): v for k, v in (headers or {}).items()}}
    }
    if tier is not None:
        env["apiKey"] = {"tier": tier}
    return env


def _allowed(rules: list[dict[str, str]], tier: str | None, method: str, path: str, **headers: str) -> bool:
    return authorize(rules, _request(tier, method, path, {k.replace("_", "-"): v for k, v in headers.items()}))[0]


# --- rendered shape ---------------------------------------------------------


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


# --- negative cases N1-N9 and the cases around them -------------------------


def test_n1_untrusted_cannot_read_sensitive(rules: list[dict[str, str]]) -> None:
    assert not _allowed(rules, "untrusted", "GET", f"{API}/{SENSITIVE}/memories/list")
    assert not _allowed(rules, "untrusted", "POST", f"{API}/{SENSITIVE}/memories/recall")


def test_n2_untrusted_cannot_write_sensitive(rules: list[dict[str, str]]) -> None:
    assert not _allowed(rules, "untrusted", "POST", f"{API}/{SENSITIVE}/memories")
    assert not _allowed(rules, "untrusted", "DELETE", f"{API}/{SENSITIVE}/documents/x")


def test_n3_trusted_cannot_write_shared(rules: list[dict[str, str]]) -> None:
    for method, suffix in [
        ("POST", "memories"),
        ("POST", "files/retain"),
        ("PATCH", "documents/x"),
        ("DELETE", "memories"),
        ("PUT", "profile"),
        ("POST", "directives"),
    ]:
        assert not _allowed(rules, "trusted", method, f"{API}/{SHARED}/{suffix}"), (method, suffix)


def test_n4_x_bank_id_header_cannot_cross_banks(rules: list[dict[str, str]]) -> None:
    path = f"{API}/{SHARED}/memories/recall"
    assert not _allowed(rules, "untrusted", "POST", path, X_Bank_Id=SENSITIVE)
    assert not _allowed(rules, "trusted", "POST", f"{API}/{SENSITIVE}/memories", X_Bank_Id=SHARED)
    assert not _allowed(rules, "untrusted", "POST", f"/hindsight/mcp/{SHARED}/", X_Bank_Id=SENSITIVE)
    assert not _allowed(rules, "trusted", "POST", f"/hindsight/mcp/{SENSITIVE}/", X_Bank_Id=SHARED)


def test_n5_mcp_cannot_reach_the_wrong_bank(rules: list[dict[str, str]]) -> None:
    # A bank_id tool argument is irrelevant: the bank is fixed by the path, and the
    # multi-bank mount (bank-less path, where the argument would matter) is never allowed.
    for tier in ("untrusted", "trusted"):
        assert not _allowed(rules, tier, "POST", "/hindsight/mcp/")
        assert not _allowed(rules, tier, "POST", "/hindsight/mcp")
    assert not _allowed(rules, "untrusted", "POST", f"/hindsight/mcp/{SENSITIVE}/")
    assert not _allowed(rules, "trusted", "POST", f"/hindsight/mcp/{SHARED}/")
    assert not _allowed(rules, "untrusted", "POST", f"/hindsight/mcp/{SHARED}x/")
    assert not _allowed(rules, "untrusted", "PUT", f"/hindsight/mcp/{SHARED}/")


def test_n6_alias_paths_are_denied(rules: list[dict[str, str]]) -> None:
    # A bank reached through an alias has a path segment that is neither real bank id.
    for tier in ("untrusted", "trusted"):
        assert not _allowed(rules, tier, "POST", f"{API}/team-alias/memories")
        assert not _allowed(rules, tier, "POST", f"{API}/{SHARED}/aliases")
        assert not _allowed(rules, tier, "POST", f"{API}/{SENSITIVE}/aliases")
        assert not _allowed(rules, tier, "GET", f"{API}/{SHARED}/aliases")
        assert not _allowed(rules, tier, "PATCH", f"{API}/{SENSITIVE}/aliases/x")
        assert not _allowed(rules, tier, "DELETE", f"{API}/{SENSITIVE}/aliases/x")


def test_n7_bank_less_chunk_and_file_routes_are_denied(rules: list[dict[str, str]]) -> None:
    for tier in ("untrusted", "trusted"):
        assert not _allowed(rules, tier, "GET", "/hindsight/v1/default/chunks/sensitive_abc")
        assert not _allowed(rules, tier, "GET", "/hindsight/v1/default/files/download/k")


@pytest.mark.parametrize("tier", [None, "", "admin", "Trusted", "trusted ", "untrusted\n"])
def test_n8_missing_or_unknown_tier_is_denied(rules: list[dict[str, str]], tier: str | None) -> None:
    assert not _allowed(rules, tier, "GET", f"{API}/{SHARED}/memories/list")
    assert not _allowed(rules, tier, "POST", f"/hindsight/mcp/{SHARED}/")
    assert not _allowed(rules, tier, "GET", "/hindsight/health")


def test_n8_roster_agent_without_a_tier_has_no_access(route: dict[str, Any]) -> None:
    keys = {k["metadata"]["agent"]: k["metadata"] for k in route["policies"]["apiKey"]["keys"]}
    assert "tier" not in keys["no-tier-agent"]
    # The gateway exposes metadata fields flattened onto apiKey; a missing field is an error.
    env = {"request": {"method": "GET", "path": f"{API}/{SHARED}/memories/list", "headers": {}}, "apiKey": {}}
    assert authorize(route["policies"]["authorization"]["rules"], env)[0] is False


def test_n9_untrusted_cannot_list_banks(rules: list[dict[str, str]]) -> None:
    for tier in ("untrusted", "trusted"):
        assert not _allowed(rules, tier, "GET", "/hindsight/v1/default/banks")
        assert not _allowed(rules, tier, "GET", "/hindsight/v1/default/banks/")


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("PUT", ""),
        ("PATCH", ""),
        ("DELETE", ""),
        ("POST", "/clone"),
        ("POST", "/import"),
        ("GET", "/export"),
        ("POST", "/transfer/import"),
        ("POST", "/transfer/export"),
        ("POST", "/document-transfer"),
        ("POST", "/document-transfer/export"),
        ("GET", "/webhooks"),
        ("POST", "/webhooks"),
        ("GET", "/config"),
        ("PATCH", "/config"),
        ("GET", "/audit-logs"),
        ("GET", "/llm-requests"),
        ("POST", "/consolidation/recover"),
        ("POST", "/new-endpoint-in-a-future-release"),
        ("GET", "/new-endpoint-in-a-future-release"),
    ],
)
def test_bank_admin_transfer_and_unknown_routes_are_denied_for_every_tier(
    rules: list[dict[str, str]], method: str, suffix: str
) -> None:
    for tier, bank in (("untrusted", SHARED), ("trusted", SHARED), ("trusted", SENSITIVE)):
        assert not _allowed(rules, tier, method, f"{API}/{bank}{suffix}"), (tier, bank, method, suffix)


@pytest.mark.parametrize(
    "path",
    [
        f"{API}/{SHARED}/../{SENSITIVE}/memories/list",
        f"{API}/{SHARED}/memories/../../{SENSITIVE}/memories/list",
        f"{API}/{SHARED}/%2e%2e/{SENSITIVE}/memories/list",
        f"{API}/{SHARED}%2F..%2F{SENSITIVE}/memories/list",
        f"{API}/%73ensitive/memories/list",
        f"{API}//{SENSITIVE}/memories/list",
        f"{API}/{SHARED}//memories/list",
        f"{API}/{SHARED}/documents/a/../../../{SENSITIVE}/memories/list",
        f"{API}/{SHARED}x/memories/list",
        f"{API}/{SHARED}-evil/memories/list",
        f"{API}/{SENSITIVE.upper()}/memories/list",
        f"/hindsightx/v1/default/banks/{SHARED}/memories/list",
        f"/hindsight/hindsight/v1/default/banks/{SHARED}/memories/list",
        f"/hindsight/v2/default/banks/{SHARED}/memories/list",
        f"/hindsight/metrics",
        f"/hindsight/",
        "/hindsight",
    ],
)
def test_path_tricks_and_lookalikes_are_denied(rules: list[dict[str, str]], path: str) -> None:
    for tier in ("untrusted", "trusted"):
        assert not _allowed(rules, tier, "GET", path), (tier, path)


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "HEAD", "OPTIONS", "get"])
def test_unlisted_methods_are_denied(rules: list[dict[str, str]], method: str) -> None:
    for tier in ("untrusted", "trusted"):
        assert not _allowed(rules, tier, method, f"{API}/{SHARED}/memories/list")


def test_tier_assignment_follows_prefix_and_exact_override() -> None:
    context = _hindsight_context(agent_tiers={"hermes-public": "untrusted"})
    assert context["agentgateway_docker_hindsight_roster_tiers"]["hermes-public"] == "untrusted"
    assert context["agentgateway_docker_hindsight_roster_tiers"]["hermes-brain"] == "trusted"
    assert "no-tier-agent" not in context["agentgateway_docker_hindsight_roster_tiers"]


def test_evaluator_rejects_deny_rules_and_unsupported_syntax() -> None:
    with pytest.raises(CelError):
        authorize([{"deny": "true"}], {})
    with pytest.raises(CelError):
        evaluate("a ? b : c", {})
    assert authorize([{"allow": "unknown.field == 1"}], {}) == (False, None)
