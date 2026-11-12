"""PR automation catalog apps use inventory values for the fixture in shadow mode."""

from pathlib import Path

import yaml


SHADOW_VARIABLES = [
    {"name": "PR_AUTOMATION_SHADOW", "secret": False, "value": "true"},
    {
        "name": "PR_AUTOMATION_REPOS",
        "secret": False,
        "value": "JacobPEvans-ai/pr-automation-fixture",
    },
]

GITHUB_READ_TOKEN = {
    "name": "GITHUB_READ_TOKEN",
    "secret": True,
    "kv": {"path": "{{ dify_docker_openbao_path }}", "field": "GITHUB_READ_ONLY_TOKEN"},
}

INVENTORY_GROUP_VARS = (
    Path(__file__).resolve().parents[2] / "inventory/group_vars/dify_group.yml"
)


def load_inventory_variables():
    return yaml.safe_load(INVENTORY_GROUP_VARS.read_text(encoding="utf-8"))


def test_pr_automation_apps_use_the_fixture_and_remain_in_shadow_mode():
    variables = load_inventory_variables()["dify_docker_agent_app_variables"]

    assert variables == {
        "pr-keeper": SHADOW_VARIABLES + [GITHUB_READ_TOKEN],
        "merge-steward": SHADOW_VARIABLES,
    }


def test_plain_values_are_public_and_the_token_comes_from_the_read_only_kv_field():
    variables = load_inventory_variables()["dify_docker_agent_app_variables"]

    for app_entries in variables.values():
        for entry in app_entries:
            if "value" in entry:
                assert entry["secret"] is False
            else:
                assert entry == GITHUB_READ_TOKEN
