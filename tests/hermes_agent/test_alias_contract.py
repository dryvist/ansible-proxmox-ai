"""The capability and alias projection stays single-sourced in the registry."""

from __future__ import annotations

from _registry import REPO_ROOT, load_registry
from _role_files import role_defaults


def test_registry_capabilities_and_transitional_aliases_are_single_sourced() -> None:
    registry = load_registry()
    local_entries = [
        entry for entry in registry
        if entry.get("enabled") and entry.get("route_group")
        and entry.get("tier") in {
            "local-large", "light", "vllm", "gpu", "hermes-local", "cpu-moe", "cpu-9b",
        }
    ]
    capabilities = {entry["route_group"] for entry in local_entries}
    # The active MLX and GPU trial routes add two capability groups.
    assert len(capabilities) == 6, capabilities

    enabled_alias_entries = [
        entry for entry in registry
        if entry.get("enabled") and entry.get("stable_aliases")
    ]
    excluded_non_servable_aliases = {
        alias for entry in enabled_alias_entries if not entry.get("servable")
        for alias in entry["stable_aliases"]
    }
    alias_pairs = [
        (alias, entry.get("route_group", entry["client_model_id"]))
        for entry in enabled_alias_entries if entry.get("servable")
        for alias in entry["stable_aliases"]
    ]
    assert len({alias for alias, _ in alias_pairs}) == len(alias_pairs), alias_pairs
    aliases = dict(alias_pairs)
    declared_aliases = {
        alias for entry in enabled_alias_entries for alias in entry["stable_aliases"]
    }
    projected_aliases = set(aliases)
    assert projected_aliases.isdisjoint(excluded_non_servable_aliases)
    assert declared_aliases == projected_aliases | excluded_non_servable_aliases
    assert len(aliases) == 18, aliases
    assert set(aliases.values()) <= capabilities

    defaults = role_defaults(REPO_ROOT / "roles" / "llm_router")
    # The public name reads the once-evaluated fact; the data is defined under the _source name.
    seeded = {entry["role"]: entry for entry in defaults["_llm_router_role_deployments_source"]}
    assert set(seeded) == {"judge", "recorder"}
    small_group = next(entry["route_group"] for entry in local_entries if entry["tier"] == "cpu-9b")
    assert aliases["fast"] == small_group
    assert aliases["cheap"] == small_group
    assert aliases["goal-judge"] == small_group
    assert seeded["judge"]["model"] == "{{ llm_router_small_model }}"
    assert seeded["judge"]["litellm_params"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert seeded["recorder"]["model"] == "{{ llm_router_small_model }}"
    assert seeded["recorder"]["litellm_params"]["turn_off_message_logging"] is True
    assert small_group in capabilities

    config = (REPO_ROOT / "roles/llm_router/templates/config.yaml.j2").read_text()
    assert "llm_router_model_group_aliases.items()" in config
    assert "model_group_alias:" in config
