"""The import-only tag selects exactly the phases the agent import needs."""

from _agent_dsl_shared import IMPORT_LOOP, _yaml

TAG = "dify_agent_import"
NEEDED = {
    "Load the fabric model registry",
    "Require router chat roles before Dify tasks",
    "Require admin credentials before the console login",
    "Headless first-run setup and console login",
    "Ensure declared MCP tool providers",
    "Import catalog agent DSLs for operator tuning",
    "Wire the tier2 rotation to each agent's webhook trigger",
    "Publish each catalog agent app as an MCP server",
}
TASKS = _yaml(IMPORT_LOOP.with_name("main.yml"))


def test_the_tag_selects_exactly_the_import_phases():
    assert {task["name"] for task in TASKS if TAG in task.get("tags", [])} == NEEDED


def test_every_tagged_include_passes_the_tag_to_its_tasks():
    for task in TASKS:
        include = task.get("args") or task.get("ansible.builtin.include_role")
        if TAG in task.get("tags", []) and "ansible.builtin.assert" not in task:
            assert TAG in include["apply"]["tags"], task["name"]
