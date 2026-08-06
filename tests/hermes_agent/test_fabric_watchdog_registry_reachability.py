"""fabric_watchdog registry-derived reachability contract.

PR #365 stopped the fallback config advertising a serving path already dead
for a month — but the authoritative state lives in a different repo
(nix-darwin), so that class of drift is invisible to any check this repo can
run at converge time. This closes the gap with periodic DETECTION instead:
probe every serving path llm-models.yml advertises as `servable: true`, using
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

# The exact combos-derivation expression from tasks/main.yml, pulled out and
# evaluated directly — a string-presence check alone would not catch a subtly
# wrong zip/map/unique chain. Every filter here is a plain Jinja2 builtin
# EXCEPT `zip`, which Ansible provides (ansible.plugins.filter.core) rather
# than base Jinja2 — registered by hand below so this runs without Ansible.
COMBOS_EXPR = (
    "(registry | selectattr('servable', 'defined') | selectattr('servable')"
    "  | map(attribute='tier') | list)"
    " | zip(registry | selectattr('servable', 'defined') | selectattr('servable')"
    "       | map(attribute='endpoint', default='default') | list)"
    " | map('join', ':') | unique | list"
)


def _combos(registry: list[dict]) -> list[str]:
    env = Environment()
    env.filters["zip"] = lambda a, b: list(zip(a, b))
    return env.compile_expression(COMBOS_EXPR)(registry=registry)


def test_only_servable_entries_produce_a_combo() -> None:
    registry = [
        {"tier": "large", "servable": True},
        {"tier": "large", "servable": False},
        {"tier": "large"},  # servable undefined entirely
    ]
    assert _combos(registry) == ["large:default"]


def test_endpoint_defaults_when_absent_and_is_kept_when_present() -> None:
    registry = [
        {"tier": "large", "servable": True},
        {"tier": "large", "servable": True, "endpoint": "cluster"},
    ]
    assert _combos(registry) == ["large:default", "large:cluster"]


def test_duplicate_combos_collapse_to_one() -> None:
    """Two servable models sharing a (tier, endpoint) — e.g. the primary and
    the goal-judge model, both tier=large with no endpoint — must not produce
    two probes of the same URL."""
    registry = [
        {"tier": "large", "servable": True},
        {"tier": "large", "servable": True},
    ]
    assert _combos(registry) == ["large:default"]


def test_registry_is_loaded_the_same_way_the_llm_router_converge_does() -> None:
    assert "include_role:\n    name: llm_router\n    tasks_from: registry.yml" in TASKS


def test_an_unmapped_combo_fails_loudly_rather_than_being_skipped() -> None:
    assert "fabric_watchdog_known_combo_urls" in TASKS
    assert "difference(fabric_watchdog_known_combo_urls.keys() | list) | length == 0" in TASKS
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
