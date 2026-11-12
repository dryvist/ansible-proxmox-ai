"""PR automation catalog apps default to the fixture in shadow mode."""

from _defaults import load_defaults


EXPECTED_VARIABLES = [
    {"name": "PR_AUTOMATION_SHADOW", "secret": False, "value": "true"},
    {
        "name": "PR_AUTOMATION_REPOS",
        "secret": False,
        "value": "JacobPEvans-ai/pr-automation-fixture",
    },
]


def test_pr_automation_apps_use_the_fixture_and_remain_in_shadow_mode():
    variables = load_defaults()["dify_docker_agent_app_variables"]

    assert variables == {
        "pr-keeper": EXPECTED_VARIABLES,
        "merge-steward": EXPECTED_VARIABLES,
    }


def test_pr_automation_defaults_contain_only_public_non_secret_values():
    variables = load_defaults()["dify_docker_agent_app_variables"]

    for app_entries in variables.values():
        assert all(entry["secret"] is False for entry in app_entries)
        assert {entry["name"] for entry in app_entries} == {
            "PR_AUTOMATION_SHADOW",
            "PR_AUTOMATION_REPOS",
        }
