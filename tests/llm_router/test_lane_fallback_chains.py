"""Lane fallback chains obey the lane contract on the registry they project.

The chains are Jinja projections in roles/llm_router/defaults/main/58-review-roles.yml.
This module cannot render Jinja, so it mirrors the registry facts those projections
read (59-key-scopes.yml, 36-hermes-cloud-segments.yml, 50-servable.yml) and checks the
mirrored chains. The mirror assumes every credential is present. Each checker is
proven on a planted violation. The role's own Ansible render path stays the source of
truth for rendered chains.

Lane contract: light never leaves local rungs (an exact empty fail-fast entry); standard
pays only over ZDR routes; critical leads with the registry subscription rung, then
paid ZDR rungs; long shares the standard chain; every lane has an explicit entry.
"""

from __future__ import annotations

import yaml

from test_alias_sources import REPO_ROOT, _Permissive

LANES = ("light", "standard", "critical", "long")
LIGHT_ALIASES = ("light", "fast", "cheap")
# Mirrors llm_router_local_registry_tiers (59-key-scopes.yml).
LOCAL_TIERS = frozenset({"local-large", "light", "vllm", "gpu", "hermes-local", "cpu-moe", "cpu-9b"})


def load_registry(root=REPO_ROOT) -> list[dict]:
    entries: list[dict] = []
    for path in sorted((root / "llm-models.d").glob("*.yml")):
        doc = yaml.load(path.read_text(), Loader=_Permissive) or {}
        for value in doc.values():
            if isinstance(value, list):
                entries += [e for e in value if isinstance(e, dict) and "client_model_id" in e]
    return entries


def _unique(items) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def group_of(entry: dict) -> str:
    return entry.get("fallback_group") or entry.get("route_group") or entry["client_model_id"]


def members(entries: list[dict], group: str) -> list[dict]:
    return [e for e in entries if group in {group_of(e), e.get("route_group"), e["client_model_id"]}]


def is_subscription(entry: dict) -> bool:
    return entry.get("price_class") == "subscription" or bool(entry.get("subscription"))


def is_free(entry: dict) -> bool:
    return (
        bool(entry.get("in_free_route"))
        or entry["client_model_id"].endswith(":free")
        or entry.get("price_class") == "free"
    )


def is_local(entry: dict) -> bool:
    return entry.get("tier") in LOCAL_TIERS and "credential_env" not in entry and not is_subscription(entry)


def light_lane_groups(entries: list[dict]) -> set[str]:
    """Route groups that light, fast, cheap and the small capability resolve to."""
    small_capability = {e["route_group"] for e in entries if e.get("tier") == "vllm" and "route_group" in e}
    aliased = {
        e["route_group"]
        for e in entries
        if set(LIGHT_ALIASES) & set(e.get("stable_aliases") or []) and "route_group" in e
    }
    return small_capability | aliased


def remote_light_members(entries: list[dict]) -> list[str]:
    return sorted(
        {e["client_model_id"] for g in light_lane_groups(entries) for e in members(entries, g) if not is_local(e)}
    )


def non_local_groups(chain: list[str], entries: list[dict]) -> list[str]:
    """Chain groups with a remote member, or with no registry member at all (fail closed)."""
    flagged = []
    for group in chain:
        found = members(entries, group)
        if not found or not all(is_local(e) for e in found):
            flagged.append(group)
    return flagged


def unzdr_paid_groups(chain: list[str], entries: list[dict]) -> list[str]:
    """Chain groups with a remote paid member that carries no ZDR claim. Subscriptions are not paid."""
    return [
        group
        for group in chain
        if any(
            not is_local(e) and not is_subscription(e) and e.get("zero_data_retention") is not True
            for e in members(entries, group)
        )
    ]


def mirrored_chains(entries: list[dict]) -> dict[str, list[str]]:
    live = [e for e in entries if e.get("enabled", True)]
    standard = _unique(
        group_of(e)
        for e in live
        if not is_free(e) and (is_local(e) or e.get("zero_data_retention") is True)
    )
    critical = _unique(
        [group_of(e) for e in live if is_subscription(e)]
        + [
            group_of(e)
            for e in live
            if not is_local(e)
            and not is_subscription(e)
            and not is_free(e)
            and e.get("zero_data_retention") is True
        ]
    )
    return {"light": [], "standard": standard, "critical": critical, "long": standard}


def test_every_lane_has_an_explicit_chain_entry() -> None:
    entries = load_registry()
    served = {alias for e in entries for alias in e.get("stable_aliases") or []}
    assert set(LANES) <= served, f"lane names not served by the registry: {sorted(set(LANES) - served)}"
    chains = mirrored_chains(entries)
    assert set(chains) == set(LANES)
    assert chains["standard"], "standard lane has no local or ZDR rung"
    assert chains["critical"], "critical lane has no subscription or ZDR paid rung"


def test_light_group_members_are_all_local() -> None:
    entries = load_registry()
    assert light_lane_groups(entries), "light resolves to no route group (anti-vacuity)"
    assert remote_light_members(entries) == []
    assert non_local_groups(mirrored_chains(entries)["light"], entries) == []


def test_planted_remote_member_in_light_group_is_caught() -> None:
    planted = {
        "client_model_id": "planted-remote",
        "tier": "openrouter",
        "route_group": "small",
        "credential_env": "OPENROUTER_API_KEY",
        "zero_data_retention": True,
        "enabled": True,
    }
    assert remote_light_members(load_registry() + [planted]) == ["planted-remote"]


def test_planted_remote_group_in_light_chain_is_caught() -> None:
    assert non_local_groups(["small", "hermes-cloud-openrouter"], load_registry()) == ["hermes-cloud-openrouter"]


def light_context_escape_targets(entries: list[dict]) -> dict[str, list[str]]:
    """Mirror of the light branch of llm_router_context_window_fallbacks: every light name maps to [].

    An empty entry is a refusal: LiteLLM runs the empty chain and never reaches the ordinary fallbacks
    (litellm/router.py:7539). The name set mirrors llm_router_light_lane_local_only_names.
    """
    live = [e for e in entries if e.get("enabled", True)]
    served = {alias for e in live for alias in e.get("stable_aliases") or []}
    small = {e["route_group"] for e in live if e.get("tier") == "vllm" and "route_group" in e}
    names = (set(LIGHT_ALIASES) & served) | small
    return {name: [] for name in sorted(names)}


def escape_leaks(targets: list[str], target_chain: list[str], entries: list[dict]) -> list[str]:
    """Escape targets whose own ordinary chain reaches a remote rung: the hop is walked with that chain
    (the hop's name wins the lookup, litellm/router_utils/fallback_event_handlers.py:397-430)."""
    return targets if non_local_groups(target_chain, entries) else []


def test_light_context_escape_is_a_refusal_not_a_hop() -> None:
    entries = load_registry()
    targets = light_context_escape_targets(entries)
    assert targets, "no light name has a context-window entry (anti-vacuity)"
    assert all(chain == [] for chain in targets.values())


def test_planted_primary_escape_leaks_through_its_chain() -> None:
    # An escape to the primary rung walks the primary's own chain, which holds remote rungs.
    entries = load_registry()
    standard = mirrored_chains(entries)["standard"]
    assert non_local_groups(standard, entries), "standard chain has no remote rung (anti-vacuity)"
    assert escape_leaks(["medium"], standard, entries) == ["medium"]


def test_planted_remote_light_escape_target_is_caught() -> None:
    entries = load_registry()
    assert non_local_groups(["medium", "hermes-cloud-openrouter"], entries) == ["hermes-cloud-openrouter"]


def test_standard_paid_rungs_are_all_zdr() -> None:
    entries = load_registry()
    standard = mirrored_chains(entries)["standard"]
    assert unzdr_paid_groups(standard, entries) == []
    assert any(not is_local(e) for g in standard for e in members(entries, g)), "no remote rung (vacuous)"


def test_planted_non_zdr_paid_rung_is_caught() -> None:
    entries = load_registry()
    assert members(entries, "codex-api"), "codex-api group missing from the registry (anti-vacuity)"
    assert unzdr_paid_groups(["codex-api"], entries) == ["codex-api"]


def test_planted_non_zdr_paid_entry_stays_out_of_standard() -> None:
    planted = {
        "client_model_id": "planted-paid",
        "tier": "openrouter",
        "route_group": "planted-paid",
        "price_class": "paid",
        "credential_env": "OPENROUTER_API_KEY",
        "zero_data_retention": False,
        "enabled": True,
    }
    entries = load_registry() + [planted]
    assert "planted-paid" not in mirrored_chains(entries)["standard"]
    assert unzdr_paid_groups(["planted-paid"], entries) == ["planted-paid"]


def test_critical_leads_with_the_subscription_then_zdr_paid_rungs() -> None:
    entries = load_registry()
    critical = mirrored_chains(entries)["critical"]
    subscriptions = _unique(group_of(e) for e in entries if e.get("enabled", True) and is_subscription(e))
    assert subscriptions, "registry has no subscription entry (anti-vacuity)"
    assert critical[: len(subscriptions)] == subscriptions
    assert unzdr_paid_groups(critical, entries) == []


def test_long_paid_rungs_are_zdr() -> None:
    entries = load_registry()
    assert unzdr_paid_groups(mirrored_chains(entries)["long"], entries) == []
