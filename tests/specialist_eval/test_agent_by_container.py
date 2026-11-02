"""The container -> agent map skips roster agents that publish no container."""

from __future__ import annotations

import pytest

from _eval_support import GROUP_VARS, ROOT, load_yaml, render

ROSTER = {
    "hermes": {"container": "hermes-agent", "owns": [], "trust_tier": "trusted"},
    "router-only": {"owns": [], "trust_tier": "untrusted", "specializations": []},
    "donna": {"container": "hermes-donna", "owns": [], "trust_tier": "trusted"},
}
EXPECTED = {"hermes-agent": "hermes", "hermes-donna": "donna"}


def _inventory_expression() -> str:
    tasks = load_yaml(ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml")
    task = next(t for t in tasks if t.get("name") == "Add AI LXC containers to inventory (proxmox_pct_remote connection)")
    return task["vars"]["_ai_agent_by_container"]


@pytest.mark.parametrize(
    ("expression", "variables"),
    [
        pytest.param(
            load_yaml(GROUP_VARS / "all/00-core.yml")["ai_agent_by_container"],
            {"ai_agents": ROSTER},
            id="group-vars",
        ),
        pytest.param(_inventory_expression(), {"_ai_agents": ROSTER}, id="inventory-loader"),
    ],
)
def test_agent_without_container_is_skipped(expression: str, variables: dict) -> None:
    assert render(expression, variables) == EXPECTED
