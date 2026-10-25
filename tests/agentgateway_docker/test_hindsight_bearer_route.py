"""Bearer callers use the same per-agent Hindsight ACL as X-Agent-Key callers."""

from __future__ import annotations

from typing import Any

from cel_subset import authorize
from hindsight_acl_support import (
    API,
    BANKS,
    KEYS,
    TRUSTED,
    TRUSTED_PEER,
    UNTRUSTED,
    _request,
    _render_hindsight,
)
from test_authz_render import REPO_ROOT, _routes


def _route_block(rendered: str, path_prefix: str, api_key_header: str) -> str:
    lines = rendered.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.startswith("    - matches:")]
    blocks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "".join(lines[start:end])
        if f"pathPrefix: {path_prefix}\n" in block and f"name: {api_key_header}" in block:
            blocks.append(block)
    assert len(blocks) == 1
    return blocks[0]


def _key_metadata(route: dict[str, Any], authorization: str) -> dict[str, str] | None:
    api_key = route["policies"]["apiKey"]
    location = api_key["location"]["header"]
    prefix = location["prefix"]
    if not authorization.startswith(prefix):
        return None
    key = authorization[len(prefix) :]
    return next((entry["metadata"] for entry in api_key["keys"] if entry["key"] == key), None)


def _bearer_allowed(route: dict[str, Any], authorization: str, method: str, path: str) -> bool:
    request: dict[str, Any] = {"request": {"method": method, "path": path, "headers": {}}}
    metadata = _key_metadata(route, authorization)
    if metadata is not None:
        request["apiKey"] = metadata
    return authorize(route["policies"]["authorization"]["rules"], request)[0]


def test_bearer_route_precedes_x_agent_key_and_shares_policy_and_backend(
    bearer_route: dict[str, Any], route: dict[str, Any]
) -> None:
    routes = _routes(_render_hindsight())
    assert routes.index(bearer_route) + 1 == routes.index(route)
    assert bearer_route["matches"][0] == {
        "path": {"pathPrefix": "/hindsight"},
        "headers": [{"name": "authorization", "value": {"regex": "^Bearer .+"}}],
    }
    assert bearer_route["policies"]["apiKey"] == {
        "mode": "strict",
        "location": {"header": {"name": "authorization", "prefix": "Bearer "}},
        "keys": route["policies"]["apiKey"]["keys"],
    }
    # Everything except the key location is shared: the other policies and the backend.
    assert {k: v for k, v in bearer_route["policies"].items() if k != "apiKey"} == {
        k: v for k, v in route["policies"].items() if k != "apiKey"
    }
    assert {k: v for k, v in bearer_route.items() if k not in ("matches", "policies")} == {
        k: v for k, v in route.items() if k not in ("matches", "policies")
    }


def test_bearer_key_uses_per_agent_tier_and_bank_rules(bearer_route: dict[str, Any]) -> None:
    authorization = f"Bearer {KEYS['hermes_brain_agentgateway_key']}"
    assert _key_metadata(bearer_route, authorization) == {"agent": TRUSTED, "tier": "trusted"}
    own_bank = BANKS[TRUSTED]
    peer_bank = BANKS[TRUSTED_PEER]
    other_tier_bank = BANKS[UNTRUSTED]

    assert _bearer_allowed(bearer_route, authorization, "GET", f"{API}/{own_bank}/memories/list")
    assert _bearer_allowed(bearer_route, authorization, "POST", f"{API}/{own_bank}/memories")
    assert _bearer_allowed(bearer_route, authorization, "GET", f"{API}/{peer_bank}/memories/list")
    assert not _bearer_allowed(bearer_route, authorization, "GET", f"{API}/{other_tier_bank}/memories/list")
    assert not _bearer_allowed(bearer_route, authorization, "POST", f"{API}/{peer_bank}/memories")


def test_unknown_bearer_key_is_rejected_by_strict_api_key_policy(bearer_route: dict[str, Any]) -> None:
    api_key = bearer_route["policies"]["apiKey"]
    assert api_key["mode"] == "strict"
    assert _key_metadata(bearer_route, "Bearer unknown-key") is None
    path = f"{API}/{BANKS[TRUSTED]}/memories/list"
    assert not _bearer_allowed(bearer_route, "Bearer unknown-key", "GET", path)


def test_request_with_neither_identity_header_matches_no_allow_rule(bearer_route: dict[str, Any]) -> None:
    rules = bearer_route["policies"]["authorization"]["rules"]
    assert not authorize(rules, _request(None, "GET", f"{API}/{BANKS[TRUSTED]}/memories/list"))[0]


def test_x_agent_key_route_render_is_unchanged() -> None:
    # Snapshot of the X-Agent-Key route as rendered before the bearer route existed.
    snapshot = (REPO_ROOT / "tests/agentgateway_docker/hindsight_x_agent_key_route.snapshot.txt").read_text()
    block = _route_block(_render_hindsight(), "/hindsight", "X-Agent-Key")
    assert (block if block.endswith("\n") else block + "\n") == snapshot
