"""Catalog apps skipped in an import-all run when their variables are not declared
or their tool provider does not name exactly one workspace provider."""

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE, _task, _yaml
from _declared_variables_shared import PRE, RESOLVE, TASKS, _env, _passes, _render

CHECK = "Assert every environment variable of the app definition with an empty value has an entry"
DECIDE = "Decide whether an app with undeclared variables is skipped by this run"
RECORD = "Record the app skipped by this run"
RESET = "Reset the per-app import skip decision"
RECONCILE = "Reconcile the catalog app and publish its workflow"
PUBLISH_RECORD = "Record the published catalog app version"
API_KEY = "Ensure the agent app API key is stored in OpenBao"
ACCUMULATOR = "Reset the catalog apps skipped for undeclared variables"
PREVIEW = "Report the declared variables a live run would set, by name only"
WIRING = TASKS / "wire_rotation_webhooks.yml"
SKIP_NAMES = "List the rotation agent app names absent from the console and skipped undeclared in this run"
RECORD_PAIR = "Record each skipped absent rotation agent pair in the run summary"
WIRE_INCLUDE = "Wire each rotation agent's webhook trigger"
SUMMARY = "Report the catalog apps skipped in this run"
PAIR_NOTE = "rotation webhook pair (app absent)"
TOOL_DECIDE = "Decide whether an app with unresolved tool providers is skipped by this run"
HOLD = "Keep the unresolved tool provider names of a skipped app for its record"
TOOL_ASSERT = "Assert every declared variable and tool provider resolved before any import"
MISSING_LIST = "List the declared tool provider placeholders missing from the app definition"
MISSING_CHECK = "Assert every declared tool provider placeholder occurs in the app definition"
REPLACE = "Replace each declared placeholder in the DSL with its tool provider id"
REMAINING_LIST = "List the declared tool provider placeholders still present after the replacement"
REMAINING_CHECK = "Assert no declared tool provider placeholder remains after the replacement"
PROVIDER_NODES = "Check each MCP tool node names a workspace provider id after the replacements"
GATE = "not dify_docker_dsl_skip_import | bool"


def _flat(text):
    return " ".join(text.split())


def _when(expression, **context):
    """A bare when scalar has no braces; render it like Ansible does."""
    return _env().from_string("{{ " + expression + " }}").render(**context).strip()


def _gated(task):
    when = task.get("when", "")
    return GATE in when


def _decides(select, undeclared, private=False):
    expression = _flat(_task(_yaml(PRE), DECIDE)["ansible.builtin.set_fact"]["dify_docker_dsl_skip_import"])
    return _render(
        expression,
        dify_docker_agent_dsl={"private": True} if private else {},
        dify_docker_agent_dsl_select=select,
        dify_docker_dsl_name="example-app",
        dify_docker_dsl_undeclared_empty=undeclared,
    )


def _tool_decides(select, unresolved, private=False):
    expression = _flat(_task(_yaml(PRE), TOOL_DECIDE)["ansible.builtin.set_fact"]["dify_docker_dsl_skip_import"])
    return _render(
        expression,
        dify_docker_agent_dsl={"private": True} if private else {},
        dify_docker_agent_dsl_select=select,
        dify_docker_dsl_name="example-app",
        dify_docker_dsl_unresolved_tools=unresolved,
    )


def test_an_import_all_run_skips_an_undeclared_app_and_still_imports_the_next_app():
    one = _yaml(IMPORT_ONE)
    names = [task.get("name") for task in one]
    # The decision is reset for every app and precedes everything that writes it,
    # so only the skipped app is held back and the loop continues.
    assert names.index(RESET) < names.index(RESOLVE) < names.index(RECONCILE)
    reset = _task(one, RESET)["ansible.builtin.set_fact"]["dify_docker_dsl_skip_import"]
    assert reset is False
    for gated in (RECONCILE, PUBLISH_RECORD, API_KEY):
        assert "not dify_docker_dsl_skip_import | bool" in _task(one, gated)["when"], gated

    assert _decides([], ["EXAMPLE_TOKEN"]) is True
    assert _decides([], []) is False
    assert _decides(["example-app"], ["EXAMPLE_TOKEN"]) is False

    accumulates = _flat(_task(_yaml(PRE), RECORD)["ansible.builtin.set_fact"]["dify_docker_dsl_skipped_apps"])

    def accumulates_to(existing):
        return _render(accumulates, dify_docker_dsl_skipped_apps=existing,
                       dify_docker_dsl_name="example-app",
                       dify_docker_dsl_undeclared_empty=["EXAMPLE_TOKEN"])

    assert accumulates_to([]) == [{"name": "example-app", "variables": ["EXAMPLE_TOKEN"], "tool_providers": []}]
    assert accumulates_to([{"name": "earlier-app", "variables": ["EXAMPLE_KEY"], "tool_providers": []}]) == [
        {"name": "earlier-app", "variables": ["EXAMPLE_KEY"], "tool_providers": []},
        {"name": "example-app", "variables": ["EXAMPLE_TOKEN"], "tool_providers": []},
    ]

    init = _task(_yaml(IMPORT_LOOP), ACCUMULATOR)
    assert init["ansible.builtin.set_fact"]["dify_docker_dsl_skipped_apps"] == []

    record = _task(_yaml(PRE), RECORD)
    assert record["when"] == "dify_docker_dsl_skip_import | bool"
    assert record["no_log"] is True
    assert "not dify_docker_dsl_skip_import | bool" in _task(_yaml(PRE), PREVIEW)["when"]


def test_an_import_all_run_skips_an_unresolved_tool_provider_app_and_still_imports_the_next_app():
    pre = _yaml(PRE)
    names = [task.get("name") for task in pre]
    one_names = [task.get("name") for task in _yaml(IMPORT_ONE)]
    # The decision is reset for every app, precedes the import resolution assert and
    # everything the skip holds back, so only the skipped app is held back and the
    # loop continues with the next one.
    assert one_names.index(RESET) < one_names.index(RESOLVE)
    assert names.index(TOOL_DECIDE) < names.index(TOOL_ASSERT) < names.index(RECORD)
    for gated in (MISSING_LIST, MISSING_CHECK, REPLACE, REMAINING_LIST, REMAINING_CHECK):
        assert _gated(_task(pre, gated)), gated
    # The MCP tool node check keeps its own gate on the MCP tool names, which the
    # hold clears for a skipped app.
    assert _task(pre, PROVIDER_NODES)["when"] == "dify_docker_dsl_mcp_tool_names | length > 0"
    assert _tool_decides([], ["example-tools"]) is True
    assert _tool_decides([], []) is False
    assert _tool_decides(["example-app"], ["example-tools"]) is False


def test_naming_the_app_in_the_selection_keeps_the_assert_fatal():
    check = _task(_yaml(PRE), CHECK)
    named = {"dify_docker_dsl_name": "example-app", "dify_docker_agent_dsl": {}}
    assert _when(_flat(check["when"]), dify_docker_agent_dsl_select=["example-app"], **named) == "True"
    assert _when(_flat(check["when"]), dify_docker_agent_dsl_select=[], **named) == "False"
    assert not _passes(check["ansible.builtin.assert"]["that"],
                       dify_docker_dsl_undeclared_empty=["EXAMPLE_TOKEN"])
    assert _passes(check["ansible.builtin.assert"]["that"], dify_docker_dsl_undeclared_empty=[])


def test_naming_the_app_in_the_selection_keeps_the_tool_provider_failure():
    pre = _yaml(PRE)
    assert _tool_decides(["example-app"], ["example-tools"]) is False
    hold_when = _flat(_task(pre, HOLD)["when"])
    assert _when(hold_when, dify_docker_dsl_skip_import="False") == "False"
    assert _when(hold_when, dify_docker_dsl_skip_import="True") == "True"
    conditions = _task(pre, TOOL_ASSERT)["ansible.builtin.assert"]["that"]
    assert not _passes(conditions, dify_docker_dsl_unresolved_names=[],
                       dify_docker_dsl_unresolved_tools=["example-tools"])
    assert _passes(conditions, dify_docker_dsl_unresolved_names=[],
                   dify_docker_dsl_unresolved_tools=[])


def test_a_private_definition_keeps_the_assert_fatal_and_is_never_skipped():
    check = _task(_yaml(PRE), CHECK)
    assert _when(_flat(check["when"]), dify_docker_agent_dsl_select=[],
                 dify_docker_dsl_name="example-app", dify_docker_agent_dsl={"private": True}) == "True"
    assert _decides([], ["EXAMPLE_TOKEN"], private=True) is False


def test_a_private_definition_keeps_the_tool_provider_failure():
    assert _tool_decides([], ["example-tools"], private=True) is False


def test_a_fully_declared_app_is_imported_as_before():
    gate = _task(_yaml(IMPORT_ONE), RECONCILE)["when"]
    assert _when(gate, dify_docker_dsl_skip_import="False") == "True"
    assert _when(gate, dify_docker_dsl_skip_import="True") == "False"


def test_a_declared_variable_without_a_value_still_fails_an_import_all_run():
    pre = _yaml(PRE)
    names = [task.get("name") for task in pre]
    # The hold runs before the assert and only ever moves the tool provider names:
    # the unresolved variable names reach the assert untouched in every run.
    assert names.index(TOOL_DECIDE) < names.index(HOLD) < names.index(TOOL_ASSERT)
    hold = _task(pre, HOLD)["ansible.builtin.set_fact"]
    assert "dify_docker_dsl_unresolved_names" not in hold
    conditions = _task(pre, TOOL_ASSERT)["ansible.builtin.assert"]["that"]
    assert not _passes(conditions, dify_docker_dsl_unresolved_names=["EXAMPLE_TOKEN"],
                       dify_docker_dsl_unresolved_tools=[])


def test_the_summary_lists_each_skipped_app_with_its_missing_variables():
    summary = _task(_yaml(WIRING), SUMMARY)
    assert summary.get("changed_when") is False
    assert "(dify_docker_dsl_skipped_apps | default([])) | length > 0" in summary["when"]
    environment = _env()
    environment.filters["zip"] = lambda *iterables: [list(pair) for pair in zip(*iterables)]
    message = environment.from_string(summary["ansible.builtin.debug"]["msg"]).render(
        dify_docker_dsl_skipped_apps=[
            {"name": "example-app", "variables": ["EXAMPLE_TOKEN"], "tool_providers": []},
            {"name": "other-app", "variables": [PAIR_NOTE], "tool_providers": []},
        ],
    )
    assert "example-app: EXAMPLE_TOKEN" in message
    assert f"other-app: {PAIR_NOTE}" in message


def test_the_summary_lists_the_unresolved_provider_names_for_the_skipped_app():
    summary = _task(_yaml(WIRING), SUMMARY)
    environment = _env()
    environment.filters["zip"] = lambda *iterables: [list(pair) for pair in zip(*iterables)]
    message = environment.from_string(summary["ansible.builtin.debug"]["msg"]).render(
        dify_docker_dsl_skipped_apps=[
            {"name": "example-app", "variables": [], "tool_providers": ["example-tools", "other-tools"]},
        ],
    )
    assert "example-app" in message
    assert "example-tools" in message and "other-tools" in message
    assert "EXAMPLE_TOKEN" not in message


def test_an_app_skipped_for_both_reasons_has_one_record_with_both_lists():
    pre = _yaml(PRE)
    names = [task.get("name") for task in pre]
    assert names.index(TOOL_DECIDE) < names.index(RECORD)
    assert _task(pre, RECORD)["when"] == "dify_docker_dsl_skip_import | bool"
    record = _flat(_task(pre, RECORD)["ansible.builtin.set_fact"]["dify_docker_dsl_skipped_apps"])
    assert _render(record, dify_docker_dsl_skipped_apps=[],
                   dify_docker_dsl_name="example-app",
                   dify_docker_dsl_undeclared_empty=["EXAMPLE_TOKEN"],
                   dify_docker_dsl_skipped_tool_providers=["example-tools"]) == [
        {"name": "example-app", "variables": ["EXAMPLE_TOKEN"], "tool_providers": ["example-tools"]}]
    # The undeclared decision keeps an app already skipped for its tool providers
    # skipped, so its one record is appended once.
    decide = _flat(_task(pre, DECIDE)["ansible.builtin.set_fact"]["dify_docker_dsl_skip_import"])
    context = {"dify_docker_agent_dsl": {}, "dify_docker_agent_dsl_select": [],
               "dify_docker_dsl_name": "example-app", "dify_docker_dsl_undeclared_empty": []}
    assert _render(decide, dify_docker_dsl_skip_import="True", **context) is True
    assert _render(decide, dify_docker_dsl_skip_import="False", **context) is False


def test_a_rotation_pair_is_skipped_only_for_an_app_skipped_here_and_absent_from_the_console():
    wiring = _yaml(WIRING)
    names = [task.get("name") for task in wiring]
    listing = _flat(_task(wiring, SKIP_NAMES)["ansible.builtin.set_fact"]["dify_docker_rotation_agent_skip_names"])

    def absent(keys, skipped, console):
        return _render(listing,
                       dify_docker_rotation_agent_keys=keys,
                       dify_docker_dsl_skipped_apps=skipped,
                       dify_docker_agent_apps_all=console)

    assert absent(["EXAMPLE_AGENT", "OTHER_AGENT"],
                  [{"name": "example-agent"}], [{"name": "other-agent"}]) == ["example-agent"]
    assert absent(["EXAMPLE_AGENT"], [], []) == []
    assert absent(["EXAMPLE_AGENT"],
                  [{"name": "example-agent"}], [{"name": "example-agent"}]) == []

    record = _flat(_task(wiring, RECORD_PAIR)["ansible.builtin.set_fact"]["dify_docker_dsl_skipped_apps"])
    assert _render(record, dify_docker_dsl_skipped_apps=[],
                   dify_docker_rotation_agent_skip_name="example-agent") == [
        {"name": "example-agent", "variables": [PAIR_NOTE], "tool_providers": []}]

    include = _task(wiring, WIRE_INCLUDE)
    assert _when(include["when"],
                 dify_docker_rotation_agent_key="EXAMPLE_AGENT",
                 dify_docker_rotation_agent_skip_names=["example-agent"]) == "False"
    assert _when(include["when"],
                 dify_docker_rotation_agent_key="OTHER_AGENT",
                 dify_docker_rotation_agent_skip_names=["example-agent"]) == "True"
    assert names.index(RECORD_PAIR) < names.index(WIRE_INCLUDE) < names.index(SUMMARY)
