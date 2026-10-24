"""Negative cases N1-N9 for the /hindsight bank ACL, and the cases around them.

See test_hindsight_acl.py for how the rendered rules are evaluated.
"""

from __future__ import annotations

from typing import Any

import pytest
from cel_subset import CelError, authorize, evaluate
from hindsight_acl_support import API, SENSITIVE, SHARED, _allowed, _hindsight_context

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
