"""fabric_watchdog registry-derived reachability contract.

Probe every serving tier llm-models.yml advertises as `servable: true`, using
the same debounced probe/alert mechanism fabric_watchdog already runs for the
MCP fabric and LLM front door.

Lives under tests/hermes_agent/ for the same reason as
test_fabric_watchdog_debounce.py: fabric_watchdog runs on the Hermes guest.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = (REPO_ROOT / "roles" / "fabric_watchdog" / "tasks" / "main.yml").read_text()

# The exact tiers-derivation expression from tasks/main.yml, pulled out and
# evaluated directly — a string-presence check alone would not catch a subtly
# wrong filter chain. Every filter here is a plain Jinja2 builtin, so it runs
# without Ansible.
TIERS_EXPR = (
    "registry | selectattr('servable', 'defined') | selectattr('servable')"
    " | map(attribute='tier') | unique | list"
)


def _tiers(registry: list[dict]) -> list[str]:
    env = Environment()
    return env.compile_expression(TIERS_EXPR)(registry=registry)


def test_only_servable_entries_produce_a_tier() -> None:
    registry = [
        {"tier": "large", "servable": True},
        {"tier": "large", "servable": False},
        {"tier": "large"},  # servable undefined entirely
    ]
    assert _tiers(registry) == ["large"]


def test_duplicate_tiers_collapse_to_one() -> None:
    """Two servable models sharing a tier must not produce
    two probes of the same URL."""
    registry = [
        {"tier": "large", "servable": True},
        {"tier": "large", "servable": True},
    ]
    assert _tiers(registry) == ["large"]


def test_registry_is_loaded_the_same_way_the_llm_router_converge_does() -> None:
    assert "include_role:\n    name: llm_router\n    tasks_from: registry.yml" in TASKS


def test_an_unmapped_tier_fails_loudly_rather_than_being_skipped() -> None:
    assert "fabric_watchdog_known_tier_urls" in TASKS
    assert "difference(fabric_watchdog_known_tier_urls.keys() | list) | length == 0" in TASKS
    assert "with no probe URL registered" in TASKS


def test_targets_are_appended_not_replaced() -> None:
    """Must not drop the existing mcp-fabric / llm-front-door / hindsight
    targets when adding the registry-derived ones."""
    assert "fabric_watchdog_targets: >-" in TASKS
    assert "{{ fabric_watchdog_targets + [{" in TASKS


def test_probe_never_touches_the_contended_inference_slot() -> None:
    """Must be a models-listing GET, never a completion call — the target
    dict itself carries no completions URL, and 401 (no auth sent) is an
    accepted UP code rather than something the probe tries to avoid."""
    target_literal = TASKS.split("fabric_watchdog_targets + [{", 1)[1].split("}]", 1)[0]
    assert "'/models'" in target_literal
    assert "'ok_codes': '200 401'" in target_literal
    for banned in ("/v1/chat/completions", "/v1/completions"):
        assert banned not in target_literal


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all checks passed")
