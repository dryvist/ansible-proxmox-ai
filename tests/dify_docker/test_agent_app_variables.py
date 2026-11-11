"""Declared catalog app variables and tool provider ids: order, secrets, pruning, check mode."""

import ast
import re
from pathlib import Path

import yaml
from jinja2 import Environment

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE, _expanded_yaml
from _defaults import load_defaults


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/dify_docker/tasks"
RECONCILE = TASKS / "import_agent_dsl_reconcile.yml"
PRE = TASKS / "import_agent_dsl_variables.yml"
ONE = TASKS / "import_agent_dsl_variable_one.yml"
APPLY = TASKS / "import_agent_dsl_variables_apply.yml"
PRUNE = TASKS / "import_agent_dsl_variables_prune.yml"
EDITED = (IMPORT_ONE, IMPORT_LOOP, RECONCILE, PRE, ONE, APPLY, PRUNE)
SELECT_STALE = "Select the older accessors to destroy, keeping the newest one not minted in this run"
PATTERN = load_defaults()["dify_docker_agent_tool_placeholder_pattern"]

RESOLVE = "Resolve declared app variables and tool provider ids before any import"
WRITE = "Write the declared app variables to the draft before publishing"
CLEAR = "Clear the declared app variable values from facts"
PRUNE_INCLUDE = "Prune older AppRole secret ids once the declared variables are published"
DECLARED_PUBLISH = "Publish the declared app variables for an unchanged catalog app"
NEW_TOP_LEVEL = {RESOLVE, WRITE, CLEAR, PRUNE_INCLUDE, DECLARED_PUBLISH}

# The import sequence before this change. Apps without declared variables must still run it.
BASELINE = [
    "Clear the previous agent DSL before fetching",
    "Resolve the private app preview name from its catalog entry",
    "Read the catalog agent DSL into memory for check mode",
    "Verify the catalog agent DSL sha256 in check mode",
    "Fetch the pinned agent DSL and verify its sha256 without redirects",
    "Write the verified private agent DSL for live import",
    "Read the verified agent DSL",
    "Select the verified in-memory or file-backed agent DSL",
    "Parse the verified DSL document and app name",
    "Remove the fetched agent DSL copy",
    "Report a DSL that failed its pin or fetch",
    "Resolve the exact existing Dify app for this catalog entry",
    "Reject duplicate Dify apps with the same catalog name",
    "Record the existing Dify app for this catalog entry",
    "Decide whether the catalog app needs an import",
    "Prepare the catalog app description",
    "Prepare the Dify import payload",
    "Preview the catalog app create or update",
    "Report an unchanged catalog app",
    "Require a workflow app mode before importing a changed catalog workflow",
    "Import the catalog DSL when reconciliation requires it",
    "Confirm the agent DSL import (DSL version mismatch)",
    "Resolve the Dify app id returned by the catalog import",
    "Require the Dify app id before publishing the catalog workflow",
    "Publish the changed catalog workflow for API callers",
    "Read the published catalog workflow for update verification",
    "Verify the existing catalog app id is unchanged",
    "Select the published catalog LLM nodes",
    "Verify every published catalog LLM node has context",
    "Load the app record after publishing",
    "Record the catalog hash after publishing the workflow",
    "Agent DSL import result",
    "Ensure the agent app API key is stored in OpenBao",
]

# Facts that hold a resolved value or a raw API response. Names-only facts are not listed,
# so the assert tasks that print a name stay visible.
SENSITIVE = (
    "dify_docker_dsl_variable_entries",
    "dify_docker_dsl_variable_values",
    "dify_docker_dsl_env_patch",
    "dify_docker_dsl_kv_read",
    "dify_docker_dsl_role_id_read",
    "dify_docker_dsl_secret_id_",
    "dify_docker_dsl_draft",
    "dify_docker_dsl_tool_providers",
    "dify_docker_dsl_tool_ids",
    "dify_docker_dsl_minted_accessors",
    "dify_docker_agent_leaf_read_result",
    "openbao_secrets_converge_writer_token",
)


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def _fact(tasks, name, fact):
    return _task(tasks, name)["ansible.builtin.set_fact"][fact]


def _env():
    environment = Environment()
    environment.filters["difference"] = lambda left, right: [item for item in left if item not in right]
    environment.filters["to_uuid"] = lambda text: f"uuid:{text}"
    environment.filters["regex_replace"] = lambda text, pattern, repl: re.sub(pattern, repl, text)
    environment.filters["regex_findall"] = lambda text, pattern: re.findall(pattern, text)
    environment.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
    environment.filters["combine"] = lambda base, extra: {**base, **extra}
    environment.filters["bool"] = lambda value: value is True or str(value).lower() in (
        "true", "yes", "on", "1"
    )
    return environment


def _render(expression, **context):
    return ast.literal_eval(_env().from_string(expression).render(**context))


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


def test_declared_variables_are_written_to_the_draft_with_secret_types_and_named_patch_only():
    apply_tasks = _yaml(APPLY)
    expression = _fact(apply_tasks, "Add each declared variable to the draft environment variable patch",
                       "dify_docker_dsl_env_patch")
    draft = {"json": {"environment_variables": [{"id": "env-1", "name": "EXAMPLE_TOKEN"},
                                                 {"id": "env-2", "name": "OTHER"}]}}
    patch = _render(expression, dify_docker_dsl_env_patch=[], dify_docker_dsl_draft=draft,
                    dify_docker_dsl_name="example-app",
                    item={"name": "EXAMPLE_TOKEN", "secret": True, "value": "x"})
    assert patch == [{"id": "env-1", "name": "EXAMPLE_TOKEN", "value": "x", "value_type": "secret"}]
    new_plain = _render(expression, dify_docker_dsl_env_patch=[], dify_docker_dsl_draft=draft,
                        dify_docker_dsl_name="example-app",
                        item={"name": "NEW_VAR", "secret": False, "value": "y"})
    assert new_plain == [{"id": "uuid:example-app:NEW_VAR", "name": "NEW_VAR", "value": "y",
                          "value_type": "string"}]
    write = _task(apply_tasks, "Write the declared variables to the draft environment variables")
    assert write["ansible.builtin.uri"]["body"]["environment_variable_patch"] == {
        "environment_variables": "{{ dify_docker_dsl_env_patch }}",
        "deleted_environment_variable_ids": [],
    }


def test_secret_ids_mint_with_the_ttl_and_keep_only_the_newest_older_id():
    assert load_defaults()["dify_docker_agent_secret_id_ttl"] == "720h"
    apply_tasks = _yaml(APPLY)
    mint = _task(apply_tasks, "Mint an AppRole secret id for each declared secret id variable")
    assert mint["ansible.builtin.uri"]["method"] == "POST"
    assert mint["ansible.builtin.uri"]["url"].endswith("/secret-id")
    assert mint["ansible.builtin.uri"]["body"] == {"ttl": "{{ dify_docker_agent_secret_id_ttl }}"}

    prune = _yaml(PRUNE)
    ages = [
        {"accessor": "a1", "created": "2026-11-01T00:00:00Z"},
        {"accessor": "a2", "created": "2026-11-02T00:00:00Z"},
        {"accessor": "a3", "created": "2026-11-03T00:00:00Z"},
        {"accessor": "a4", "created": "2026-11-04T00:00:00Z"},
    ]
    stale = _render(_fact(prune, SELECT_STALE, "dify_docker_dsl_secret_id_stale"),
                    dify_docker_dsl_secret_id_ages=ages)
    assert sorted(stale) == ["a1", "a2", "a3"]
    collect = _fact(prune, "Collect each accessor with its creation time to the second",
                    "dify_docker_dsl_secret_id_ages")
    assert _render(collect, dify_docker_dsl_secret_id_ages=[],
                   item={"item": "acc-1", "json": {"data": {"creation_time": "2026-11-10T19:30:01.123456789Z"}}}
                   ) == [{"accessor": "acc-1", "created": "2026-11-10T19:30:01Z"}]
    lookups = _task(prune, "Look up the creation time of each secret id accessor")["loop"]
    assert _render(lookups, dify_docker_dsl_secret_id_list={"json": {"data": {"keys": ["a", "b"]}}}) == ["a", "b"]
    assert _render(lookups, dify_docker_dsl_secret_id_list={"json": {"errors": []}}) == []


def test_check_mode_resolves_reads_only_and_never_mints_or_writes():
    for path in (PRE, ONE):
        for task in _yaml(path):
            uri = task.get("ansible.builtin.uri")
            if uri:
                assert uri.get("method") == "GET", f"{path.name}: {task.get('name')}"
    reconcile = _yaml(RECONCILE)
    for name in (WRITE, PRUNE_INCLUDE, DECLARED_PUBLISH):
        assert "not ansible_check_mode" in _task(reconcile, name)["when"], name
    report = _task(_yaml(PRE), "Report the declared variables a live run would set, by name only")
    assert "ansible_check_mode" in report["when"]


def test_publish_runs_after_the_write_and_prune_runs_after_the_publish():
    names = [task.get("name") for task in _expanded_yaml(IMPORT_ONE)]
    import_index = names.index("Import the catalog DSL when reconciliation requires it")
    publish_index = names.index("Publish the changed catalog workflow for API callers")
    assert names.index(RESOLVE) < import_index < names.index(WRITE) < publish_index
    assert names.index(WRITE) < names.index(DECLARED_PUBLISH) < names.index(PRUNE_INCLUDE)
    assert publish_index < names.index(PRUNE_INCLUDE)


def test_every_task_that_holds_a_resolved_value_is_no_log():
    for path in EDITED:
        for task in _yaml(path):
            text = yaml.safe_dump(task)
            if any(name in text for name in SENSITIVE):
                assert task.get("no_log") is True, f"{path.name}: {task.get('name')}"


def test_null_missing_or_empty_kv_field_is_unresolved_and_never_the_string_none():
    keep = _fact(_yaml(ONE), "Keep the declared KV field value", "dify_docker_dsl_variable_values")
    unresolved = _fact(_yaml(PRE), "List the declared variables that have no value yet",
                       "dify_docker_dsl_unresolved_names")
    entry = {"name": "NULLED", "secret": True, "kv": {"path": "example/app", "field": "token"}}
    for secret in ({"token": None}, {}, {"token": ""}):
        values = _render(keep, dify_docker_dsl_variable_values=[], dify_docker_dsl_kv_read={"secret": secret},
                         dify_docker_dsl_variable_entry=entry)
        assert values == [{"name": "NULLED", "secret": True, "value": ""}], secret
        assert _render(unresolved, dify_docker_dsl_resolve_names=["NULLED"],
                       dify_docker_dsl_variable_values=values) == ["NULLED"], secret
    present = _render(keep, dify_docker_dsl_variable_values=[], dify_docker_dsl_kv_read={"secret": {"token": "v"}},
                      dify_docker_dsl_variable_entry=entry)
    assert present == [{"name": "NULLED", "secret": True, "value": "v"}]


def test_prune_destroys_only_older_accessors_and_never_one_minted_in_the_run():
    select = _fact(_yaml(PRUNE), SELECT_STALE, "dify_docker_dsl_secret_id_stale")
    older = [
        {"accessor": "old-1", "created": "2026-11-01T00:00:00Z"},
        {"accessor": "old-2", "created": "2026-11-02T00:00:00Z"},
    ]
    for minted_count in (1, 2, 3):
        minted = [
            {"accessor": f"new-{number}", "created": f"2026-11-10T00:00:0{number}Z"}
            for number in range(1, minted_count + 1)
        ]
        stale = _render(select, dify_docker_dsl_secret_id_ages=older + minted,
                        dify_docker_dsl_minted_accessors=[entry["accessor"] for entry in minted])
        assert stale == ["old-1"], minted_count
    assert _render(select, dify_docker_dsl_secret_id_ages=older) == ["old-1"]
    assert _render(select, dify_docker_dsl_secret_id_ages=older[:1]) == []


def test_minted_accessors_accumulate_across_apps_without_secret_ids():
    record = _fact(_yaml(APPLY), "Record each minted secret id accessor for this run's prune protection",
                   "dify_docker_dsl_minted_accessors")
    result = _render(record, dify_docker_dsl_minted_accessors=["new-1"],
                     item={"json": {"data": {"secret_id_accessor": "new-2", "secret_id": "SECRET-VALUE"}}})
    assert result == ["new-1", "new-2"]
    assert "SECRET-VALUE" not in str(result)
    assert _render(record, item={"json": {"data": {"secret_id_accessor": "new-1"}}}) == ["new-1"]


def test_minted_accessor_list_is_reset_once_per_run_and_kept_across_app_iterations():
    loop = _yaml(IMPORT_LOOP)
    names = [task.get("name") for task in loop]
    reset = names.index("Reset the secret id accessors minted in this run")
    assert reset < names.index("Reconcile each catalog agent DSL")
    assert loop[reset]["no_log"] is True
    clear = _task(_yaml(IMPORT_ONE), "Clear the declared app variable values from facts")
    assert "dify_docker_dsl_minted_accessors" not in clear["ansible.builtin.set_fact"]


def _passes(conditions, **context):
    return all(
        _env().from_string("{{ " + condition + " }}").render(**context).strip() == "True"
        for condition in conditions
    )


def test_unmapped_placeholder_or_secret_fails_before_the_import_and_names_only():
    names = [task.get("name") for task in _expanded_yaml(IMPORT_ONE)]
    assert names.index(RESOLVE) < names.index("Import the catalog DSL when reconciliation requires it")
    pre = _yaml(PRE)
    pre_names = [task.get("name") for task in pre]
    check_name = "Assert the app definition has no unmapped placeholder and no secret without an entry"
    assert pre_names.index("Replace each declared placeholder in the DSL with its tool provider id") < (
        pre_names.index(check_name))
    check = _task(pre, check_name)
    assert check.get("no_log") is not True
    assert "ansible_check_mode" not in str(check.get("when", ""))
    assert "dify_docker_dsl_document" not in check["ansible.builtin.assert"]["fail_msg"]
    assert "dify_docker_dsl_content" not in check["ansible.builtin.assert"]["fail_msg"]

    placeholders = _fact(pre, "List the placeholder tokens still present after the declared replacements",
                         "dify_docker_dsl_unmapped_placeholders")
    assert _render(placeholders, dify_docker_dsl_content="a __EXAMPLE_TOKEN__ b __OTHER__ c __EXAMPLE_TOKEN__",
                   dify_docker_agent_tool_placeholder_pattern=PATTERN) == ["__EXAMPLE_TOKEN__", "__OTHER__"]
    assert _render(placeholders, dify_docker_dsl_content="name: id-1",
                   dify_docker_agent_tool_placeholder_pattern=PATTERN) == []

    secrets = _fact(pre, "List the secret environment variables of the app definition with no declared entry",
                    "dify_docker_dsl_unmapped_secrets")
    document = {"workflow": {"environment_variables": [
        {"name": "API_KEY", "value_type": "secret", "value": ""},
        {"name": "MAPPED", "value_type": "secret", "value": ""},
        {"name": "PLAIN", "value_type": "string", "value": ""},
        {"name": "FILLED", "value_type": "secret", "value": "not-a-real-value"},
    ]}}
    assert _render(secrets, dify_docker_dsl_document=document, dify_docker_dsl_declared_names=["MAPPED"]) == [
        "API_KEY"
    ]

    conditions = check["ansible.builtin.assert"]["that"]
    unmapped = {"dify_docker_dsl_unmapped_placeholders": ["__EXAMPLE_TOKEN__"], "dify_docker_dsl_unmapped_secrets": []}
    assert not _passes(conditions, **unmapped)
    assert _passes(conditions, dify_docker_dsl_unmapped_placeholders=[], dify_docker_dsl_unmapped_secrets=[])
    message = _env().from_string(check["ansible.builtin.assert"]["fail_msg"]).render(
        dify_docker_dsl_name="example-app", dify_docker_dsl_unmapped_placeholders=["__EXAMPLE_TOKEN__"],
        dify_docker_dsl_unmapped_secrets=["API_KEY"])
    assert "__EXAMPLE_TOKEN__" in message and "API_KEY" in message
    assert "not-a-real-value" not in message


def test_declared_checks_run_for_entries_placeholders_or_empty_secrets_and_skip_clean_apps():
    defaults = load_defaults()
    gate = defaults["dify_docker_dsl_declared_check_needed"]
    clean = {"workflow": {"environment_variables": [{"name": "PLAIN", "value_type": "string", "value": ""}]}}
    common = {
        "dify_docker_dsl_variable_entries": [],
        "dify_docker_dsl_tool_provider_map": {},
        "dify_docker_agent_tool_placeholder_pattern": defaults["dify_docker_agent_tool_placeholder_pattern"],
        "dify_docker_dsl_content": "name: example",
        "dify_docker_dsl_document": clean,
    }

    def render(**overrides):
        context = {**common, **overrides}
        context["dify_docker_dsl_has_variables"] = bool(context["dify_docker_dsl_variable_entries"]) or bool(
            context["dify_docker_dsl_tool_provider_map"])
        return _env().from_string(gate).render(**context).strip()

    assert render() == "False"
    assert render(dify_docker_dsl_content="a __EXAMPLE_TOKEN__") == "True"
    assert render(dify_docker_dsl_document={"workflow": {"environment_variables": [
        {"name": "API_KEY", "value_type": "secret", "value": ""}]}}) == "True"
    assert render(dify_docker_dsl_variable_entries=[{"name": "X"}]) == "True"
    assert render(dify_docker_dsl_content="a __lower__ and x__EXAMPLE__y") == "False"
    pattern = re.compile(defaults["dify_docker_agent_tool_placeholder_pattern"])
    assert pattern.search("a __EXAMPLE_TOOL__ b") and not pattern.search("x__EXAMPLE__y")
    include = next(task for task in _yaml(IMPORT_ONE)
                   if task.get("name") == "Resolve declared app variables and tool provider ids before any import")
    assert include["when"][0] == "dify_docker_dsl_declared_check_needed | bool"


def test_apps_without_declared_variables_keep_the_same_task_sequence():
    names = [task.get("name") for task in _expanded_yaml(IMPORT_ONE)]
    assert [name for name in names if name not in NEW_TOP_LEVEL] == BASELINE
    defaults = load_defaults()
    assert defaults["dify_docker_agent_app_variables"] == {}
    assert defaults["dify_docker_agent_tool_providers"] == {}
    has_variables = defaults["dify_docker_dsl_has_variables"]
    assert _env().from_string(has_variables).render(
        dify_docker_dsl_variable_entries=[], dify_docker_dsl_tool_provider_map={}
    ).strip() == "False"
    assert _env().from_string(has_variables).render(
        dify_docker_dsl_variable_entries=[{"name": "X"}], dify_docker_dsl_tool_provider_map={}
    ).strip() == "True"
    assert _env().from_string(has_variables).render(
        dify_docker_dsl_variable_entries=[], dify_docker_dsl_tool_provider_map={"__A__": "tool"}
    ).strip() == "True"
