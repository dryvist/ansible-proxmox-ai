"""PR automation catalog apps use inventory values for the fixture in shadow mode."""

from pathlib import Path

import yaml


EXPECTED_VARIABLES = [
    {"name": "PR_AUTOMATION_SHADOW", "secret": False, "value": "true"},
    {
        "name": "PR_AUTOMATION_REPOS",
        "secret": False,
        "value": "{{ lookup('env', 'DIFY_PR_AUTOMATION_REPOS') | default('example-org/example-repo', true) }}",
    },
]

INVENTORY_GROUP_VARS = (
    Path(__file__).resolve().parents[2] / "inventory/group_vars/dify_group.yml"
)


def load_inventory_variables():
    return yaml.safe_load(INVENTORY_GROUP_VARS.read_text(encoding="utf-8"))


def test_pr_automation_apps_use_the_fixture_and_remain_in_shadow_mode():
    variables = load_inventory_variables()["dify_docker_agent_app_variables"]

    assert variables == {
        "pr-keeper": EXPECTED_VARIABLES,
        "merge-steward": EXPECTED_VARIABLES,
    }


def test_pr_automation_inventory_values_contain_only_public_non_secret_values():
    variables = load_inventory_variables()["dify_docker_agent_app_variables"]

    for app_entries in variables.values():
        assert all(entry["secret"] is False for entry in app_entries)
        assert {entry["name"] for entry in app_entries} == {
            "PR_AUTOMATION_SHADOW",
            "PR_AUTOMATION_REPOS",
        }
