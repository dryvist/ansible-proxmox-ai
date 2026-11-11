"""Declared catalog app variables: MCP tool node provider ids, derived facts and the per-app clear."""

from _agent_dsl_shared import IMPORT_ONE
from _declared_variables_shared import (
    DERIVE,
    PRE,
    PROVIDER,
    TASKS,
    _fact,
    _render,
    _task,
    _yaml,
)
from _defaults import load_defaults

KNOWN_ID = "3f6c2a1e-0b7d-4c8e-9a51-2d4f6e8b0c11"
UNKNOWN_ID = "7d9e0f1a-2b3c-4d5e-8f6a-9b0c1d2e3f40"
COLLECT = "Collect the MCP tool nodes whose provider id is not a workspace provider id"


def _collect(pairs, workspace_ids):
    expression = _fact(_yaml(PROVIDER), COLLECT, "dify_docker_dsl_bad_provider_nodes")
    bad = []
    for pair in pairs:
        bad = _render(expression, dify_docker_dsl_bad_provider_nodes=bad, dify_docker_dsl_mcp_node=pair,
                      dify_docker_dsl_workspace_ids=workspace_ids)
    return bad


def test_mcp_tool_nodes_are_selected_from_the_parsed_definition_only():
    expression = _fact(_yaml(DERIVE), "Select the MCP tool nodes of this app's definition",
                       "dify_docker_dsl_mcp_nodes")
    document = {"workflow": {"graph": {"nodes": [
        {"id": "a", "data": {"type": "tool", "provider_type": "mcp", "provider_id": KNOWN_ID, "tool_name": "t1"}},
        {"id": "b", "data": {"type": "tool", "provider_type": "builtin", "provider_id": "time"}},
        {"id": "c", "data": {"type": "llm", "provider_type": "mcp"}},
    ]}}}
    selected = _render(expression, dify_docker_dsl_document=document)
    assert [node["id"] for node in selected] == ["a"]
    assert _render(expression) == []


def test_derived_names_and_ids_fall_back_to_placeholders_for_missing_fields():
    names = _fact(_yaml(DERIVE), "Derive the MCP tool node names and provider ids once",
                  "dify_docker_dsl_mcp_tool_names")
    ids = _fact(_yaml(DERIVE), "Derive the MCP tool node names and provider ids once",
                "dify_docker_dsl_mcp_provider_ids")
    nodes = [{"data": {"type": "tool", "provider_type": "mcp"}}]
    assert _render(names, dify_docker_dsl_mcp_nodes=nodes) == ["unnamed"]
    assert _render(ids, dify_docker_dsl_mcp_nodes=nodes) == [""]


def test_provider_ids_map_through_the_declared_replacements_in_order():
    expression = _fact(_yaml(PROVIDER), "Map each MCP tool node provider id through the declared replacements",
                       "dify_docker_dsl_mcp_provider_ids")
    assert _render(expression, dify_docker_dsl_mcp_provider_ids=["EXAMPLE_TOOL", "other-id"],
                   dify_docker_dsl_tool_entry={"key": "EXAMPLE_TOOL"},
                   dify_docker_dsl_tool_ids={"EXAMPLE_TOOL": [KNOWN_ID]}) == [KNOWN_ID, "other-id"]


def test_workspace_ids_are_the_ids_the_workspace_lists():
    expression = _fact(_yaml(PROVIDER), "Read the workspace provider ids", "dify_docker_dsl_workspace_ids")
    assert _render(expression, dify_docker_dsl_tool_providers={"json": [{"id": KNOWN_ID, "name": "a"},
                                                                       {"id": "x-1", "name": "b"}]}) == [
        KNOWN_ID, "x-1"]


def test_a_node_outside_the_workspace_list_is_named_and_no_provider_id_is_ever_printed():
    bad = _collect([["tool-a", KNOWN_ID], ["tool-b", UNKNOWN_ID], ["tool-c", "EXAMPLE_TOOL"]], [KNOWN_ID])
    assert bad == ["tool-b", "tool-c"]
    assert UNKNOWN_ID not in str(bad) and "EXAMPLE_TOOL" not in str(bad)


def test_the_workspace_check_passes_when_every_node_is_listed():
    assert _collect([["tool-a", KNOWN_ID]], [KNOWN_ID]) == []


def test_the_provider_check_is_gated_on_mcp_tool_names_and_runs_after_the_replacements():
    pre = _yaml(PRE)
    names = [task.get("name") for task in pre]
    include_name = "Check each MCP tool node names a workspace provider id after the replacements"
    include = _task(pre, include_name)
    assert include["when"] == "dify_docker_dsl_mcp_tool_names | length > 0"
    assert include["ansible.builtin.include_tasks"] == "import_agent_dsl_provider_nodes.yml"
    assert names.index("Assert no declared tool provider placeholder remains after the replacement") < (
        names.index(include_name))
    assert names.index(include_name) < names.index(
        "List the environment variables of the app definition with an empty value and no entry")
    get = _task(pre, "List the workspace tool providers for the declared placeholders")
    assert "dify_docker_dsl_mcp_tool_names | length > 0" in get["when"]


def test_the_placeholder_pattern_is_gone_and_no_task_scans_code_node_bodies():
    assert "dify_docker_agent_tool_placeholder_pattern" not in load_defaults()
    assert "dify_docker_agent_provider_id_pattern" not in load_defaults()
    for path in TASKS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "placeholder_pattern" not in text and "provider_id_pattern" not in text, path.name
        assert "regex_findall" not in text, path.name


def test_the_provider_check_states_its_built_in_limit_in_the_shortcut_form():
    text = PROVIDER.read_text(encoding="utf-8")
    assert "shortcut: only provider_type mcp nodes are checked" in text
    assert "Upgrade when" in text


def test_the_derived_facts_are_cleared_at_the_end_of_every_app():
    clear = _task(_yaml(IMPORT_ONE), "Clear the declared app variable values from facts")
    assert "when" not in clear
    for fact in ("dify_docker_dsl_mcp_nodes", "dify_docker_dsl_mcp_provider_ids",
                 "dify_docker_dsl_workspace_ids", "dify_docker_dsl_bad_provider_nodes"):
        assert fact in clear["ansible.builtin.set_fact"], fact
