"""Catalog apps skipped in an import-all run when their variables are not declared."""

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE, _task, _yaml
from _declared_variables_shared import PRE, RESOLVE, TASKS, _env, _passes, _render

CHECK = "Assert every environment variable of the app definition with an empty value has an entry"
DECIDE = "Decide whether an app with undeclared variables is skipped by this run"
RECORD = "Record the app skipped for its undeclared variables"
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


def _flat(text):
    return " ".join(text.split())


def _when(expression, **context):
    """A bare when scalar has no braces; render it like Ansible does."""
    return _env().from_string("{{ " + expression + " }}").render(**context).strip()


def _decides(select, undeclared):
    expression = _flat(_task(_yaml(PRE), DECIDE)["ansible.builtin.set_fact"]["dify_docker_dsl_skip_import"])
    return _render(
        expression,
        dify_docker_agent_dsl_select=select,
        dify_docker_dsl_name="example-app",
        dify_docker_dsl_undeclared_empty=undeclared,
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

    assert accumulates_to([]) == [{"name": "example-app", "variables": ["EXAMPLE_TOKEN"]}]
    assert accumulates_to([{"name": "earlier-app", "variables": ["EXAMPLE_KEY"]}]) == [
        {"name": "earlier-app", "variables": ["EXAMPLE_KEY"]},
        {"name": "example-app", "variables": ["EXAMPLE_TOKEN"]},
    ]

    init = _task(_yaml(IMPORT_LOOP), ACCUMULATOR)
    assert init["ansible.builtin.set_fact"]["dify_docker_dsl_skipped_apps"] == []

    record = _task(_yaml(PRE), RECORD)
    assert record["when"] == "dify_docker_dsl_skip_import | bool"
    assert record["no_log"] is True
    assert "not dify_docker_dsl_skip_import | bool" in _task(_yaml(PRE), PREVIEW)["when"]


def test_naming_the_app_in_the_selection_keeps_the_assert_fatal():
    check = _task(_yaml(PRE), CHECK)
    assert check["when"] == "dify_docker_dsl_name in dify_docker_agent_dsl_select"
    assert not _passes(check["ansible.builtin.assert"]["that"],
                       dify_docker_dsl_undeclared_empty=["EXAMPLE_TOKEN"])
    assert _passes(check["ansible.builtin.assert"]["that"], dify_docker_dsl_undeclared_empty=[])


def test_a_fully_declared_app_is_imported_as_before():
    gate = _task(_yaml(IMPORT_ONE), RECONCILE)["when"]
    assert _when(gate, dify_docker_dsl_skip_import="False") == "True"
    assert _when(gate, dify_docker_dsl_skip_import="True") == "False"


def test_the_summary_lists_each_skipped_app_with_its_missing_variables():
    summary = _task(_yaml(WIRING), SUMMARY)
    assert summary.get("changed_when") is False
    assert "(dify_docker_dsl_skipped_apps | default([])) | length > 0" in summary["when"]
    environment = _env()
    environment.filters["zip"] = lambda *iterables: [list(pair) for pair in zip(*iterables)]
    message = environment.from_string(summary["ansible.builtin.debug"]["msg"]).render(
        dify_docker_dsl_skipped_apps=[
            {"name": "example-app", "variables": ["EXAMPLE_TOKEN"]},
            {"name": "other-app", "variables": [PAIR_NOTE]},
        ],
    )
    assert "example-app: EXAMPLE_TOKEN" in message
    assert f"other-app: {PAIR_NOTE}" in message


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
        {"name": "example-agent", "variables": [PAIR_NOTE]}]

    include = _task(wiring, WIRE_INCLUDE)
    assert _when(include["when"],
                 dify_docker_rotation_agent_key="EXAMPLE_AGENT",
                 dify_docker_rotation_agent_skip_names=["example-agent"]) == "False"
    assert _when(include["when"],
                 dify_docker_rotation_agent_key="OTHER_AGENT",
                 dify_docker_rotation_agent_skip_names=["example-agent"]) == "True"
    assert names.index(RECORD_PAIR) < names.index(WIRE_INCLUDE) < names.index(SUMMARY)
