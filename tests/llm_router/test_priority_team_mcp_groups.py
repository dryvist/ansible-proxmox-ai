"""Priority teams allow the MCP access groups their keys declare. Runs bare or under pytest."""
import ast
from pathlib import Path

import jinja2
import yaml

ROLE = Path(__file__).resolve().parents[2] / "roles" / "llm_router"
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main" / "56d-priority-tiers.yml").read_text())
TASKS = yaml.safe_load((ROLE / "tasks" / "seed-priority-teams.yml").read_text())


def _task(name: str) -> dict:
    return next(t for t in TASKS if t["name"] == name)


def test_team_groups_are_the_union_of_their_keys_groups():
    keys = [
        {"alias": "a", "team_id": "tier-1", "object_permission": {"mcp_access_groups": ["hub-public"]}},
        {"alias": "b", "team_id": "tier-1",
         "object_permission": {"mcp_access_groups": ["hub-public", "hub-homelab-read"]}},
        {"alias": "c", "team_id": "tier-0", "object_permission": {"mcp_servers": ["no-mcp-servers"]}},
        {"alias": "d", "team_id": "tier-2", "object_permission": None},
    ]
    template = jinja2.Environment().from_string(DEFAULTS["llm_router_priority_team_mcp_access_groups"])
    groups = ast.literal_eval(template.render(_llm_router_virtual_keys_without_values=keys).strip())
    assert groups == {"tier-1": ["hub-homelab-read", "hub-public"], "tier-0": [], "tier-2": []}


def test_priority_team_writes_send_the_team_groups():
    create = _task("Create absent priority teams")["ansible.builtin.uri"]["body"]
    update_task = _task("Reconcile existing priority team tags")
    update = update_task["ansible.builtin.uri"]["body"]
    assert "llm_router_priority_team_mcp_access_groups" in create["object_permission"]["mcp_access_groups"]
    assert update["object_permission"]["mcp_access_groups"] == "{{ _mcp_groups }}"
    assert "llm_router_priority_team_mcp_access_groups" in update_task["vars"]["_mcp_groups"]
    assert "_mcp_groups != _live_mcp_groups" in " ".join(update_task["when"])


if __name__ == "__main__":
    test_team_groups_are_the_union_of_their_keys_groups()
    test_priority_team_writes_send_the_team_groups()
    print("all checks passed")
