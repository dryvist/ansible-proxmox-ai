"""Negative cases N1-N9 for the /hindsight bank ACL, and the cases around them.

See test_hindsight_acl.py for how the rendered rules are evaluated.
"""

from __future__ import annotations

from typing import Any

import pytest
from cel_subset import CelError, authorize, evaluate
from hindsight_acl_support import (
    API,
    BANKS,
    TIERS,
    TRUSTED,
    TRUSTED_PEER,
    UNTRUSTED,
    UNTRUSTED_PEER,
    _allowed,
)

OWN, PEER, OTHER = BANKS[TRUSTED], BANKS[TRUSTED_PEER], BANKS[UNTRUSTED]
WRITES = [
    ("POST", "memories"),
    ("POST", "files/retain"),
    ("PATCH", "documents/x"),
    ("DELETE", "memories"),
    ("PUT", "profile"),
    ("POST", "directives"),
]

# --- negative cases N1-N9 and the cases around them -------------------------


def test_n1_cross_tier_reads_are_denied_in_both_directions(rules: list[dict[str, str]]) -> None:
    for caller, bank in ((UNTRUSTED, OWN), (UNTRUSTED, PEER), (UNTRUSTED_PEER, OWN), (TRUSTED, OTHER), (TRUSTED_PEER, OTHER)):
        assert not _allowed(rules, caller, "GET", f"{API}/{bank}/memories/list"), (caller, bank)
        assert not _allowed(rules, caller, "POST", f"{API}/{bank}/memories/recall"), (caller, bank)


def test_n2_cross_tier_writes_are_denied(rules: list[dict[str, str]]) -> None:
    for caller, bank in ((UNTRUSTED, OWN), (UNTRUSTED_PEER, PEER), (TRUSTED, OTHER)):
        for method, suffix in WRITES:
            assert not _allowed(rules, caller, method, f"{API}/{bank}/{suffix}"), (caller, bank, suffix)


def test_n3_same_tier_peers_cannot_write_each_others_banks(rules: list[dict[str, str]]) -> None:
    for caller, bank in ((TRUSTED, PEER), (TRUSTED_PEER, OWN), (UNTRUSTED, BANKS[UNTRUSTED_PEER])):
        for method, suffix in WRITES:
            assert not _allowed(rules, caller, method, f"{API}/{bank}/{suffix}"), (caller, bank, method, suffix)


def test_n4_x_bank_id_header_cannot_cross_banks(rules: list[dict[str, str]]) -> None:
    path = f"{API}/{OWN}/memories/recall"
    assert not _allowed(rules, TRUSTED, "POST", path, X_Bank_Id=OTHER)
    assert not _allowed(rules, TRUSTED, "POST", path, X_Bank_Id=PEER)
    assert not _allowed(rules, TRUSTED, "POST", f"{API}/{OWN}/memories", X_Bank_Id=PEER)
    assert not _allowed(rules, TRUSTED, "POST", f"/hindsight/mcp/{OWN}/", X_Bank_Id=OTHER)
    # A header naming an allowed bank cannot unlock a path into a denied one.
    assert not _allowed(rules, UNTRUSTED, "POST", f"{API}/{OWN}/memories/recall", X_Bank_Id=OTHER)


def test_n5_mcp_cannot_reach_the_wrong_bank(rules: list[dict[str, str]]) -> None:
    # A bank_id tool argument is irrelevant: the bank is fixed by the path, and the
    # multi-bank mount (bank-less path, where the argument would matter) is never allowed.
    for caller in (TRUSTED, UNTRUSTED):
        assert not _allowed(rules, caller, "POST", "/hindsight/mcp/")
        assert not _allowed(rules, caller, "POST", "/hindsight/mcp")
    # The mount can write, so a same-tier peer and a cross-tier caller are both refused.
    assert not _allowed(rules, TRUSTED, "POST", f"/hindsight/mcp/{PEER}/")
    assert not _allowed(rules, UNTRUSTED, "POST", f"/hindsight/mcp/{OWN}/")
    assert not _allowed(rules, TRUSTED, "POST", f"/hindsight/mcp/{OTHER}/")
    assert not _allowed(rules, TRUSTED, "POST", f"/hindsight/mcp/{OWN}x/")
    assert not _allowed(rules, TRUSTED, "PUT", f"/hindsight/mcp/{OWN}/")


def test_n6_alias_paths_are_denied(rules: list[dict[str, str]]) -> None:
    # A bank reached through an alias has a path segment that is no real bank id.
    for caller in (TRUSTED, UNTRUSTED):
        own = BANKS[caller]
        assert not _allowed(rules, caller, "POST", f"{API}/team-alias/memories")
        assert not _allowed(rules, caller, "POST", f"{API}/{own}/aliases")
        assert not _allowed(rules, caller, "GET", f"{API}/{own}/aliases")
        assert not _allowed(rules, caller, "PATCH", f"{API}/{own}/aliases/x")
        assert not _allowed(rules, caller, "DELETE", f"{API}/{own}/aliases/x")


def test_n7_bank_less_chunk_and_file_routes_are_denied(rules: list[dict[str, str]]) -> None:
    for caller in (TRUSTED, UNTRUSTED):
        assert not _allowed(rules, caller, "GET", f"/hindsight/v1/default/chunks/{BANKS[caller]}_abc")
        assert not _allowed(rules, caller, "GET", "/hindsight/v1/default/files/download/k")


@pytest.mark.parametrize("tier", [None, "", "admin", "Trusted", "trusted ", "untrusted\n"])
def test_n8_missing_or_unknown_tier_is_denied(rules: list[dict[str, str]], tier: str | None) -> None:
    # Even the owner of a bank loses it when its tier claim is missing or malformed.
    assert not _allowed(rules, TRUSTED, "GET", f"{API}/{OWN}/memories/list", tier=tier)
    assert not _allowed(rules, TRUSTED, "POST", f"{API}/{OWN}/memories", tier=tier)
    assert not _allowed(rules, TRUSTED, "POST", f"/hindsight/mcp/{OWN}/", tier=tier)
    assert not _allowed(rules, TRUSTED, "GET", "/hindsight/health", tier=tier)


def test_n8_unknown_caller_and_tierless_roster_agent_have_no_access(route: dict[str, Any]) -> None:
    keys = {k["metadata"]["agent"]: k["metadata"] for k in route["policies"]["apiKey"]["keys"]}
    assert "tier" not in keys["no-tier-agent"]
    rules = route["policies"]["authorization"]["rules"]
    # The gateway exposes metadata fields flattened onto apiKey; a missing field is an error.
    env = {"request": {"method": "GET", "path": f"{API}/{OWN}/memories/list", "headers": {}}, "apiKey": {}}
    assert authorize(rules, env)[0] is False
    for bank in BANKS.values():
        assert not _allowed(rules, "no-tier-agent", "GET", f"{API}/{bank}/memories/list", tier=None)
        assert not _allowed(rules, "no-tier-agent", "POST", f"/hindsight/mcp/{bank}/", tier=None)
        # An agent name outside the roster owns nothing, even claiming a real tier.
        assert not _allowed(rules, "ghost-agent", "POST", f"{API}/{bank}/memories", tier=TIERS[TRUSTED])
    assert not _allowed(rules, "no-tier-agent", "GET", "/hindsight/health", tier=None)
    assert not _allowed(rules, "no-tier-agent", "GET", "/hindsight/version", tier=None)


def test_n9_nobody_can_list_banks(rules: list[dict[str, str]]) -> None:
    for caller in (TRUSTED, UNTRUSTED):
        assert not _allowed(rules, caller, "GET", "/hindsight/v1/default/banks")
        assert not _allowed(rules, caller, "GET", "/hindsight/v1/default/banks/")


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
def test_bank_admin_transfer_and_unknown_routes_are_denied_for_every_agent(
    rules: list[dict[str, str]], method: str, suffix: str
) -> None:
    for caller in BANKS:
        for bank in BANKS.values():
            assert not _allowed(rules, caller, method, f"{API}/{bank}{suffix}"), (caller, bank, method, suffix)


@pytest.mark.parametrize(
    "path",
    [
        f"{API}/{OWN}/../{OTHER}/memories/list",
        f"{API}/{OWN}/memories/../../{OTHER}/memories/list",
        f"{API}/{OWN}/%2e%2e/{OTHER}/memories/list",
        f"{API}/{OWN}%2F..%2F{OTHER}/memories/list",
        f"{API}/%68ermes-brain-trusted/memories/list",
        f"{API}//{OTHER}/memories/list",
        f"{API}/{OWN}//memories/list",
        f"{API}/{OWN}/documents/a/../../../{OTHER}/memories/list",
        f"{API}/{OWN}x/memories/list",
        f"{API}/{OWN}-evil/memories/list",
        f"{API}/{OWN.upper()}/memories/list",
        f"/hindsightx/v1/default/banks/{OWN}/memories/list",
        f"/hindsight/hindsight/v1/default/banks/{OWN}/memories/list",
        f"/hindsight/v2/default/banks/{OWN}/memories/list",
        "/hindsight/metrics",
        "/hindsight/",
        "/hindsight",
    ],
)
def test_path_tricks_and_lookalikes_are_denied(rules: list[dict[str, str]], path: str) -> None:
    assert not _allowed(rules, UNTRUSTED, "GET", path), path
    # The trusted caller owns OWN; only its own exact, clean path may pass.
    if path != f"{API}/{OWN}/memories/list":
        assert not _allowed(rules, TRUSTED, "GET", path), path


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "HEAD", "OPTIONS", "get"])
def test_unlisted_methods_are_denied(rules: list[dict[str, str]], method: str) -> None:
    for caller in (TRUSTED, UNTRUSTED):
        assert not _allowed(rules, caller, method, f"{API}/{BANKS[caller]}/memories/list")


def test_evaluator_rejects_deny_rules_and_unsupported_syntax() -> None:
    with pytest.raises(CelError):
        authorize([{"deny": "true"}], {})
    with pytest.raises(CelError):
        evaluate("a ? b : c", {})
    assert authorize([{"allow": "unknown.field == 1"}], {}) == (False, None)
