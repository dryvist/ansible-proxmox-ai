"""Declared catalog app variables: resolution, placeholder, tool provider, template token and _MODE checks."""

from _agent_dsl_shared import IMPORT_ONE, _expanded_yaml
from _declared_variables_shared import (
    PRE,
    RESOLVE,
    _env,
    _fact,
    _passes,
    _render,
    _task,
    _yaml,
)
from _defaults import load_defaults


def test_unresolved_variable_or_tool_id_fails_before_the_import_runs():
    names = [task.get("name") for task in _expanded_yaml(IMPORT_ONE)]
    assert names.index(RESOLVE) < names.index("Import the catalog DSL when reconciliation requires it")
    pre = _yaml(PRE)
    unresolved = _fact(pre, "List the declared variables that have no value yet",
                       "dify_docker_dsl_unresolved_names")
    assert _render(
        unresolved,
        dify_docker_dsl_resolve_names=["API_KEY", "EMPTY"],
        dify_docker_dsl_variable_values=[{"name": "API_KEY", "value": "x"}, {"name": "EMPTY", "value": ""}],
    ) == ["EMPTY"]
    unresolved_tools = _fact(pre, "List the tool provider names with no single workspace match",
                             "dify_docker_dsl_unresolved_tools")
    context = {"dify_docker_dsl_unresolved_tools": [], "dify_docker_dsl_tool_entry": {
        "key": "__TOOL_A__", "value": "tool-a"}}
    assert _render(unresolved_tools, **context, dify_docker_dsl_tool_ids={"__TOOL_A__": []}) == ["tool-a"]
    assert _render(unresolved_tools, **context, dify_docker_dsl_tool_ids={"__TOOL_A__": ["id-1", "id-2"]}) == [
        "tool-a"
    ]
    assert _render(unresolved_tools, **context, dify_docker_dsl_tool_ids={"__TOOL_A__": ["id-1"]}) == []
    assert_task = _task(pre, "Assert every declared variable and tool provider resolved before any import")
    assert "dify_docker_dsl_unresolved_names | length == 0" in assert_task["ansible.builtin.assert"]["that"]
    assert "dify_docker_dsl_unresolved_tools | length == 0" in assert_task["ansible.builtin.assert"]["that"]


def test_entries_naming_zero_or_several_sources_are_rejected_by_name_only():
    collect = _fact(_yaml(PRE), "Collect declared variables that do not name exactly one source",
                    "dify_docker_dsl_bad_names")
    two_sources = {"name": "TWO", "value": "1", "kv": {"path": "p", "field": "f"}}
    no_source = {"name": "NONE", "secret": True}
    valid = {"name": "OK", "value": "1"}
    assert _render(collect, dify_docker_dsl_bad_names=[], dify_docker_dsl_variable_entry=two_sources) == ["TWO"]
    assert _render(collect, dify_docker_dsl_bad_names=[], dify_docker_dsl_variable_entry=no_source) == ["NONE"]
    assert _render(collect, dify_docker_dsl_bad_names=[], dify_docker_dsl_variable_entry=valid) == []


def test_tool_provider_placeholder_is_replaced_by_its_single_workspace_match():
    pre = _yaml(PRE)
    ids = _fact(pre, "Find the workspace tool provider id for each placeholder", "dify_docker_dsl_tool_ids")
    providers = {"json": [{"id": "id-1", "name": "example-tool"}, {"id": "id-2", "name": "other"}]}
    assert _render(ids, dify_docker_dsl_tool_ids={}, dify_docker_dsl_tool_providers=providers,
                   dify_docker_dsl_tool_entry={"key": "__A__", "value": "example-tool"}) == {"__A__": ["id-1"]}
    substitute = _fact(pre, "Replace each declared placeholder in the DSL with its tool provider id",
                       "dify_docker_dsl_content")
    assert _env().from_string(substitute).render(
        dify_docker_dsl_content="name: __A__",
        dify_docker_dsl_tool_ids={"__A__": ["id-1"]},
        dify_docker_dsl_tool_entry={"key": "__A__", "value": "example-tool"},
    ).strip() == "name: id-1"


def test_undeclared_empty_variable_fails_before_the_import_and_names_only():
    names = [task.get("name") for task in _expanded_yaml(IMPORT_ONE)]
    assert names.index(RESOLVE) < names.index("Import the catalog DSL when reconciliation requires it")
    pre = _yaml(PRE)
    pre_names = [task.get("name") for task in pre]
    check_name = "Assert every environment variable of the app definition with an empty value has an entry"
    assert pre_names.index("Replace each declared placeholder in the DSL with its tool provider id") < (
        pre_names.index(check_name))
    check = _task(pre, check_name)
    assert check.get("no_log") is not True
    assert "ansible_check_mode" not in str(check.get("when", ""))
    assert "dify_docker_dsl_document" not in check["ansible.builtin.assert"]["fail_msg"]
    assert "dify_docker_dsl_content" not in check["ansible.builtin.assert"]["fail_msg"]

    undeclared = _fact(pre, "List the environment variables of the app definition with an empty value and no entry",
                       "dify_docker_dsl_undeclared_empty")
    document = {"workflow": {"environment_variables": [
        {"name": "API_KEY", "value_type": "secret", "value": ""},
        {"name": "MAPPED", "value_type": "secret", "value": ""},
        {"name": "PLAIN", "value_type": "string", "value": ""},
        {"name": "FILLED", "value_type": "secret", "value": "not-a-real-value"},
    ]}}
    assert _render(undeclared, dify_docker_dsl_document=document,
                   dify_docker_dsl_declared_names=["MAPPED"]) == ["API_KEY", "PLAIN"]
    assert _render(undeclared, dify_docker_dsl_document=document,
                   dify_docker_dsl_declared_names=["MAPPED", "API_KEY", "PLAIN"]) == []

    conditions = check["ansible.builtin.assert"]["that"]
    assert not _passes(conditions, dify_docker_dsl_undeclared_empty=["PLAIN"])
    assert _passes(conditions, dify_docker_dsl_undeclared_empty=[])
    message = _env().from_string(check["ansible.builtin.assert"]["fail_msg"]).render(
        dify_docker_dsl_name="example-app", dify_docker_dsl_undeclared_empty=["PLAIN"])
    assert "PLAIN" in message
    assert "not-a-real-value" not in message


def test_tool_provider_key_must_occur_in_the_definition_and_not_remain_after_replacement():
    pre = _yaml(PRE)
    names = [task.get("name") for task in pre]
    substitute = names.index("Replace each declared placeholder in the DSL with its tool provider id")
    assert names.index("Assert every declared tool provider placeholder occurs in the app definition") < substitute
    assert substitute < names.index("Assert no declared tool provider placeholder remains after the replacement")
    tool_map = {"__TOOL_A__": "tool-a", "__TOOL_B__": "tool-b"}
    missing = _fact(pre, "List the declared tool provider placeholders missing from the app definition",
                    "dify_docker_dsl_missing_tool_keys")
    remaining = _fact(pre, "List the declared tool provider placeholders still present after the replacement",
                      "dify_docker_dsl_remaining_tool_keys")
    assert _render(missing, dify_docker_dsl_tool_provider_map=tool_map,
                   dify_docker_dsl_content="x __TOOL_A__ y") == ["__TOOL_B__"]
    assert _render(missing, dify_docker_dsl_tool_provider_map=tool_map,
                   dify_docker_dsl_content="x __TOOL_A__ __TOOL_B__") == []
    assert _render(remaining, dify_docker_dsl_tool_provider_map=tool_map,
                   dify_docker_dsl_content="x id-1 __TOOL_B__") == ["__TOOL_B__"]
    assert _render(remaining, dify_docker_dsl_tool_provider_map=tool_map,
                   dify_docker_dsl_content="x id-1 id-2") == []
    for name, key in (("Assert every declared tool provider placeholder occurs in the app definition",
                       "dify_docker_dsl_missing_tool_keys"),
                      ("Assert no declared tool provider placeholder remains after the replacement",
                       "dify_docker_dsl_remaining_tool_keys")):
        check = _task(pre, name)
        assert check.get("no_log") is not True
        assert "dify_docker_dsl_content" not in check["ansible.builtin.assert"]["fail_msg"]
        assert _passes(check["ansible.builtin.assert"]["that"], **{key: []})
        assert not _passes(check["ansible.builtin.assert"]["that"], **{key: ["__TOOL_B__"]})


def test_template_token_literal_values_stay_unresolved():
    unresolved = _fact(_yaml(PRE), "List the declared variables that have no value yet",
                       "dify_docker_dsl_unresolved_names")
    names = ["TEMPLATE", "SPACED", "LOWER", "PLAIN"]
    values = [{"name": "TEMPLATE", "value": "<APPROVED_VALUE>"}, {"name": "SPACED", "value": "<APPROVED VALUE>"},
              {"name": "LOWER", "value": "<lower>"}, {"name": "PLAIN", "value": "approved"}]
    assert _render(unresolved, dify_docker_dsl_resolve_names=names,
                   dify_docker_dsl_variable_values=values) == ["TEMPLATE"]
    assert _render(unresolved, dify_docker_dsl_resolve_names=["TEMPLATE"],
                   dify_docker_dsl_variable_values=[{"name": "TEMPLATE", "value": "APPROVED_VALUE"}]) == []


def test_declared_checks_run_for_entries_mcp_nodes_or_empty_variables_and_skip_clean_apps():
    gate = load_defaults()["dify_docker_dsl_declared_check_needed"]
    clean = {"workflow": {"environment_variables": [{"name": "PLAIN", "value_type": "string", "value": "x"}]}}
    common = {
        "dify_docker_dsl_variable_entries": [],
        "dify_docker_dsl_tool_provider_map": {},
        "dify_docker_dsl_mcp_tool_names": [],
        "dify_docker_dsl_document": clean,
    }

    def render(**overrides):
        context = {**common, **overrides}
        context["dify_docker_dsl_has_variables"] = bool(context["dify_docker_dsl_variable_entries"]) or bool(
            context["dify_docker_dsl_tool_provider_map"])
        return _env().from_string(gate).render(**context).strip()

    assert render() == "False"
    assert render(dify_docker_dsl_mcp_tool_names=["example-tool"]) == "True"
    assert render(dify_docker_dsl_document={"workflow": {"environment_variables": [
        {"name": "API_KEY", "value_type": "secret", "value": ""}]}}) == "True"
    assert render(dify_docker_dsl_document={"workflow": {"environment_variables": [
        {"name": "PLAIN", "value_type": "string", "value": ""}]}}) == "True"
    assert render(dify_docker_dsl_document={}) == "False"
    assert render(dify_docker_dsl_variable_entries=[{"name": "X"}]) == "True"
    include = next(task for task in _yaml(IMPORT_ONE)
                   if task.get("name") == "Resolve declared app variables and tool provider ids before any import")
    assert include["when"][0] == "dify_docker_dsl_declared_check_needed | bool"


def test_mode_variables_must_be_shadow_unless_the_app_is_listed_as_live():
    pre = _yaml(PRE)
    collect = _fact(pre, "Collect declared _MODE variables that do not resolve to shadow for an app not listed as live",
                    "dify_docker_dsl_bad_mode_names")

    def bad(entry, values, live=(), suffix="_MODE", shadow="shadow"):
        return _render(collect, dify_docker_dsl_bad_mode_names=[], dify_docker_dsl_name="example-app",
                       dify_docker_agent_live_apps=list(live), dify_docker_dsl_variable_values=values,
                       dify_docker_dsl_mode_entry=entry, dify_docker_agent_mode_suffix=suffix,
                       dify_docker_agent_mode_shadow_value=shadow)

    for value, flagged in (("live", True), (" LIVE ", True), ("Shadow", False), ("shadow ", False)):
        entry = {"name": "EXAMPLE_MODE", "value": value}
        assert bad(entry, [{"name": "EXAMPLE_MODE", "value": value}]) == (["EXAMPLE_MODE"] if flagged else []), value
    # The suffix and the shadow value come from variables, so other values apply the same way.
    flag = {"name": "EXAMPLE_FLAG", "value": "off"}
    assert bad(flag, [{"name": "EXAMPLE_FLAG", "value": "off"}], suffix="_FLAG", shadow="off") == []
    assert bad(flag, [{"name": "EXAMPLE_FLAG", "value": "on"}], suffix="_FLAG", shadow="off") == ["EXAMPLE_FLAG"]
    assert bad({"name": "EXAMPLE_MODE", "value": "live"}, [{"name": "EXAMPLE_MODE", "value": "live"}],
               suffix="_FLAG") == []
    listed = {"name": "EXAMPLE_MODE", "value": "live"}
    assert bad(listed, [{"name": "EXAMPLE_MODE", "value": "live"}], live=["example-app"]) == []
    other = {"name": "EXAMPLE_TOKEN", "value": "live"}
    assert bad(other, [{"name": "EXAMPLE_TOKEN", "value": "live"}]) == []
    minted = {"name": "EXAMPLE_MODE", "approle_secret_id": {"approle": "agent-example"}}
    assert bad(minted, []) == ["EXAMPLE_MODE"]

    check = _task(pre, "Assert each declared _MODE variable is shadow unless the app is listed as live")
    assert check.get("no_log") is not True
    assert _passes(check["ansible.builtin.assert"]["that"], dify_docker_dsl_bad_mode_names=[])
    assert not _passes(check["ansible.builtin.assert"]["that"], dify_docker_dsl_bad_mode_names=["EXAMPLE_MODE"])
    message = _env().from_string(check["ansible.builtin.assert"]["fail_msg"]).render(
        dify_docker_dsl_name="example-app", dify_docker_dsl_bad_mode_names=["EXAMPLE_MODE"])
    assert "example-app" in message and "EXAMPLE_MODE" in message
