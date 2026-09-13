"""The registry-side half of the Hermes brain contract.

Split out of test_goal_mode_contract.py along its real seam: everything here
reads llm-models.d/ and the llm_router role, nothing here renders a
hermes_agent template. STRUCTURE is pinned, never a model name or alias — an
alias is written once, in the registry, and
tests/llm_router/test_registry_retype_scan.py fails the build on a second copy.
"""

from __future__ import annotations

import yaml

from _registry import REPO_ROOT, backend_for_alias, backend_for_role, load_registry
from _role_files import role_defaults


def test_static_aliases_and_roles_follow_the_registry() -> None:
    registry = load_registry()
    group_vars = yaml.safe_load((REPO_ROOT / "inventory/group_vars/all.yml").read_text())
    router_defaults = role_defaults(REPO_ROOT / "roles" / "llm_router")
    router_config = (REPO_ROOT / "roles/llm_router/templates/config.yaml.j2").read_text()

    hermes_backend = backend_for_role(registry, "primary")
    judge_alias = group_vars["hermes_goal_judge_model"]
    judge_backend = backend_for_alias(registry, judge_alias)
    assert judge_backend != hermes_backend

    # Physical aliases belong to the entries they point at. The Hermes brain
    # selector is intentionally not one of them: it is a native LiteLLM
    # complexity-router deployment, not duplicated configuration for a
    # physical backend. Split the same way roles/llm_router splits them. An
    # alias on a SERVABLE entry renders as a static model_group_alias and is
    # bound by the role's two render-time asserts; an alias on any other entry
    # is a ROLE seeded into the router database. Asserting the union would let
    # a stray static alias hide behind a legitimate role name, which is the
    # case this test exists to catch.
    # The hermes-router tier is a second admitted class alongside `servable`
    # (roles/llm_router/defaults/main/50-servable.yml's llm_router_alias_pairs):
    # the local complexity router dispatches to llm_router_routine_model /
    # llm_router_primary_model rather than answering directly, so an alias on
    # it renders as a static model_group_alias too, not a DB role.
    aliases = {
        alias: entry["client_model_id"]
        for entry in registry
        if entry.get("enabled") and (entry.get("servable") or entry.get("tier") == "hermes-router")
        for alias in entry.get("stable_aliases", [])
    }
    db_role_aliases = {
        alias: entry["client_model_id"]
        for entry in registry
        if entry.get("enabled") and not entry.get("servable") and entry.get("tier") != "hermes-router"
        for alias in entry.get("stable_aliases", [])
    }
    # The count and every target's servability are what a stray alias would
    # break, so a new consumer-facing name still lands here as a reviewed edit.
    assert aliases, "no static alias loaded; nothing below is checked"
    assert len(aliases) == 6
    assert aliases[judge_alias] == judge_backend
    # The brain is reached by alias too (the judge does not share it, above).
    assert hermes_backend in aliases.values()
    # The document tier is reached by image content parts, not by a selector
    # var, so it has no hermes_* binding to assert — only that a name a human
    # picks in the model list resolves to the vision entry.
    assert backend_for_role(registry, "ocr") in aliases.values()
    # A role is a caller-facing name, so an accidental one is as costly as an
    # accidental static alias: exactly one, the delegation role.
    assert len(db_role_aliases) == 1
    assert set(db_role_aliases.values()) & set(aliases.values()) == set()

    hermes_router = next(
        entry
        for entry in registry
        if entry.get("enabled") and entry["client_model_id"] == group_vars["hermes_brain_model"]
    )
    assert hermes_router["tier"] == "hermes-router"
    # The deployment name is the entry's own provider and upstream id; a drift
    # between the three registry fields is what this catches.
    assert hermes_router["litellm_model_name"] == (
        f"{hermes_router['provider']}/{hermes_router['upstream_model_id']}"
    )
    # The only alias this entry may carry is `default` — the hermes-router
    # carve-out this test's `aliases` bucket already admits above; a second
    # name here would be an undeclared consumer-facing alias.
    assert hermes_router.get("stable_aliases") == ["default"]

    # Both selectors must be declared servable, or the alias indirection just
    # moves the 404 one level down.
    #
    # `servable` is deliberately NOT `enabled`: every large-tier entry is
    # enabled (the router offers it), only these are servable (the backend
    # answers for it). Conflating them yields a 404, not an answer.
    #
    # The contract is a BICONDITIONAL — servable if and only if the entry names
    # a serving_role — and BOTH sides derive from the registry. This used to
    # name the expected ids through by_role["primary"]/["small"], which held
    # only while the serving host ran exactly one warm model: since 2026-08-14
    # it holds two, and a second servable model with no role to name it would
    # have failed a true statement. Deriving keeps the check real rather than
    # loosening it — flipping `servable` on a dead entry, or dropping it from a
    # live one, still fails here.
    expected_servable = [
        entry["client_model_id"]
        for entry in registry
        if entry.get("enabled") and "serving_role" in entry
    ]
    assert [
        entry["client_model_id"] for entry in registry if entry.get("servable")
    ] == expected_servable
    assert hermes_backend in expected_servable
    assert judge_backend in expected_servable
    # And so must every static alias target, or an alias is a 404 with a name
    # — except the local complexity router itself, admitted above for the
    # same reason llm_router_alias_pairs admits it.
    hermes_router_ids = [
        entry["client_model_id"]
        for entry in registry
        if entry.get("enabled") and entry.get("tier") == "hermes-router"
    ]
    assert set(aliases.values()) <= set(expected_servable) | set(hermes_router_ids)
    hermes_entries = [
        entry for entry in registry if entry["client_model_id"] == hermes_backend
    ]
    assert len(hermes_entries) == 1
    assert hermes_entries[0]["context_window"] == 65536

    # The registry is the SOLE spelling of a model name or key field: the role's
    # defaults project it and must never re-type one. A literal here is exactly
    # the drift this indirection exists to prevent, so it fails the build rather
    # than waiting for a live 404. Values only — the defaults' prose may of
    # course still discuss the tiers.
    router_defaults_values = yaml.dump(router_defaults, allow_unicode=True)
    for entry in registry:
        for field in ("client_model_id", "upstream_model_id", "key_field"):
            if field in entry:
                assert entry[field] not in router_defaults_values, (
                    f"{entry[field]} is re-typed in roles/llm_router/defaults/main.yml; "
                    "derive it from llm-models.d/ instead"
                )
    assert router_defaults["llm_router_num_retries"] == 0
    # 429 = "the slot is busy", never "the work is impossible", so the router
    # absorbs it rather than failing the caller (#175). Not 0 — that setting
    # killed a cron mid-generation on 2026-07-24.
    assert router_defaults["llm_router_rate_limit_retries"] == 8
    assert "model_group_alias:" in router_config
    assert "llm_router_model_group_aliases.items()" in router_config


def test_credential_gated_entries_declare_their_own_credential() -> None:
    """Every entry of a credential-gated tier names its own credential.

    The env, probe and role projections read `credential_env` and `key_field`
    bare off the entry; there is deliberately no per-tier default to fall back
    on, because a default shared across a loop that mixes tiers is how one
    provider's entry gets silently credentialed with another provider's key.
    So the registry has to carry both fields on every gated entry, and this is
    where an entry that omits one fails.

    Anti-vacuity: remove either field from any opencode, hermes-cloud or
    openrouter entry and `missing` is non-empty. The gated set is asserted
    non-empty first, so a registry slice that failed to load cannot pass by
    having nothing to check. The gated tiers are the same four the render
    parity guard exempts from enabled-but-unrendered for being key-gated
    (roles/llm_router/tasks/assert-registry-render-parity.yml).
    """
    registry = load_registry()
    gated_tiers = {"opencode", "hermes-cloud", "hermes-cloud-router", "openrouter"}
    gated = [entry for entry in registry if entry["tier"] in gated_tiers]
    assert gated, "no credential-gated registry entries loaded; nothing was checked"
    missing = [
        (entry["client_model_id"], field)
        for entry in gated
        for field in ("credential_env", "key_field")
        if not entry.get(field)
    ]
    assert not missing, (
        f"credential-gated entries without their own credential fields: {missing}; "
        "declare credential_env and key_field on the entry (docs/LLM_MODELS_SCHEMA.md)"
    )
