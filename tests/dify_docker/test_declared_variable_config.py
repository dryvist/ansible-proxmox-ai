"""Declared catalog app variables: per-app leaf config, the per-app declaration reset and the issuer login."""

import json

import pytest

from _agent_dsl_shared import IMPORT_ONE, _expanded_yaml
from _declared_variables_shared import (
    APPLY,
    CLEAR,
    CONFIG,
    DERIVE,
    EDITED,
    ISSUER,
    LEAF_INCLUDE,
    LEAF_RESET,
    NEW_TOP_LEVEL,
    ONE,
    PRE,
    PRUNE,
    RESOLVE,
    _env,
    _fact,
    _passes,
    _render,
    _task,
    _yaml,
)
from _defaults import load_defaults


def test_leaf_declarations_reset_before_each_app_so_none_carry_over():
    tasks = _expanded_yaml(IMPORT_ONE)
    names = [task.get("name") for task in tasks]
    assert names.index(LEAF_RESET) < names.index(LEAF_INCLUDE)
    reset = _task(tasks, LEAF_RESET)
    assert reset["when"] == "dify_docker_agent_app_config_from_leaf | bool"
    assert reset["ansible.builtin.set_fact"] == {
        "dify_docker_dsl_variable_entries": [], "dify_docker_dsl_tool_provider_map": {}}
    assert reset.get("no_log") is True


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


def _leaf_parse_expressions():
    block = _task(_yaml(CONFIG), "Parse the declared fields of the per-app leaf")
    variables = _task(block["block"], "Parse the variables field of the per-app leaf")
    providers = _task(block["block"], "Parse the tool providers field of the per-app leaf")
    return block, variables["ansible.builtin.set_fact"]["dify_docker_dsl_config_variables"], (
        providers["ansible.builtin.set_fact"]["dify_docker_dsl_config_tool_providers"])


def test_leaf_config_is_off_by_default_and_gated_on_the_flag():
    assert load_defaults()["dify_docker_agent_app_config_from_leaf"] is False
    include = next(task for task in _yaml(IMPORT_ONE) if task.get("name") == LEAF_INCLUDE)
    assert include["when"][0] == "dify_docker_agent_app_config_from_leaf | bool"
    assert include["ansible.builtin.include_tasks"] == "import_agent_dsl_config_leaf.yml"
    assert include.get("no_log") is True


def test_leaf_fields_parse_to_the_same_entries_as_the_same_data_in_inventory():
    _, variables, providers = _leaf_parse_expressions()
    inventory_entries = [
        {"name": "EXAMPLE_TOKEN", "secret": True, "kv": {"path": "example/app", "field": "token"}},
        {"name": "EXAMPLE_ROLE_ID", "approle_role_id": {"approle": "agent-example"}},
    ]
    inventory_tools = {"__EXAMPLE_TOOL__": "example-tool"}
    leaf = {"secret": {"variables_json": json.dumps(inventory_entries),
                       "tool_providers_json": json.dumps(inventory_tools)}}
    assert _render(variables, dify_docker_dsl_config_leaf=leaf) == inventory_entries
    assert _render(providers, dify_docker_dsl_config_leaf=leaf) == inventory_tools


def test_absent_empty_invalid_or_mistyped_leaf_fields_fail_closed():
    block, variables, providers = _leaf_parse_expressions()
    for leaf in ({}, {"secret": {}}, {"secret": {"variables_json": ""}}, {"secret": {"variables_json": "[not json"}}):
        with pytest.raises(Exception):
            _env().from_string(variables).render(dify_docker_dsl_config_leaf=leaf)
    for leaf in ({"secret": {"tool_providers_json": ""}}, {"secret": {"tool_providers_json": "{bad"}}):
        with pytest.raises(Exception):
            _env().from_string(providers).render(dify_docker_dsl_config_leaf=leaf)
    shapes = _task(block["block"], "Check the per-app leaf fields have the list and object shapes")
    conditions = shapes["ansible.builtin.assert"]["that"]
    assert _passes(conditions, dify_docker_dsl_config_variables=[{"name": "X"}],
                   dify_docker_dsl_config_tool_providers={})
    for mistyped_variables, mistyped_tools in (({"name": "X"}, {}), ("text", {}), ([], []), ([], "text")):
        assert not _passes(conditions, dify_docker_dsl_config_variables=mistyped_variables,
                           dify_docker_dsl_config_tool_providers=mistyped_tools)
    message = block["rescue"][0]["ansible.builtin.fail"]["msg"]
    assert "dify_docker_dsl_config" not in message and "dify_docker_dsl_name" in message
    assert block["rescue"][0].get("no_log") is not True


def test_leaf_and_inventory_may_not_both_declare_entries_for_one_app():
    conflict = _task(_yaml(CONFIG), "Assert the inventory declares no entries for this app when the leaf supplies them")
    conditions = conflict["ansible.builtin.assert"]["that"]

    def clear(variables, tools):
        return all(_env().from_string("{{ " + condition + " }}").render(
            dify_docker_agent_app_variables=variables, dify_docker_agent_tool_providers=tools,
            dify_docker_dsl_name="example-app").strip() == "True" for condition in conditions)

    assert clear({}, {})
    assert clear({"other-app": [{"name": "X"}]}, {"other-app": {"__T__": "t"}})
    assert not clear({"example-app": [{"name": "X"}]}, {})
    assert not clear({}, {"example-app": {"__TOOL_A__": "tool-a"}})


def test_leaf_read_uses_the_app_leaf_path_is_no_log_and_is_cleared_per_app():
    read = _task(_yaml(CONFIG), "Read the per-app leaf that holds the declared config")
    assert read["vars"]["dify_docker_agent_leaf_read_path"] == (
        "{{ dify_docker_openbao_path }}/agent-apps/{{ dify_docker_dsl_name }}")
    assert read["vars"]["dify_docker_agent_leaf_read_register"] == "dify_docker_dsl_config_leaf"
    assert read.get("no_log") is True
    clear = _task(_yaml(IMPORT_ONE), "Clear the declared app variable values from facts")
    assert "dify_docker_dsl_config_leaf" in clear["ansible.builtin.set_fact"]


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


def test_the_login_runs_first_in_the_resolve_stage_and_only_for_an_app_with_approle_entries():
    login = _yaml(PRE)[0]
    assert login["ansible.builtin.include_tasks"] == "import_agent_dsl_issuer_login.yml"
    assert login["when"] == "dify_docker_dsl_has_approle_entries | bool"
    derived = _fact(_yaml(DERIVE), "Record whether the app declares an AppRole-sourced variable",
                    "dify_docker_dsl_has_approle_entries")
    assert _env().from_string(derived).render(
        dify_docker_dsl_variable_entries=[{"name": "X", "kv": {"path": "p", "field": "f"}}]).strip() == "False"
    for source in ("approle_role_id", "approle_secret_id"):
        assert _env().from_string(derived).render(
            dify_docker_dsl_variable_entries=[{"name": "X", source: {"approle": "a"}}]).strip() == "True"


def test_missing_issuer_credentials_fail_before_the_import_and_name_only_the_variables():
    names = [task.get("name") for task in _yaml(ISSUER)]
    assert names == [
        "Record whether the issuer AppRole credentials are set",
        "Assert the issuer AppRole credentials are set",
        "Log in with the issuer AppRole",
        "Keep the issuer token for this app",
        "Assert the issuer AppRole login returned a token",
    ]
    top = [task.get("name") for task in _yaml(IMPORT_ONE)]
    assert top.index(RESOLVE) < top.index("Reconcile the catalog app and publish its workflow")
    ready = _fact(_yaml(ISSUER), "Record whether the issuer AppRole credentials are set", "dify_docker_dsl_issuer_ready")
    for role, secret, expected in (("r", "s", "True"), ("", "s", "False"), ("r", "", "False")):
        assert _env().from_string(ready).render(dify_docker_agent_issuer_role_id=role,
                                                dify_docker_agent_issuer_secret_id=secret).strip() == expected
    fail = _task(_yaml(ISSUER), "Assert the issuer AppRole credentials are set")["ansible.builtin.assert"]["fail_msg"]
    message = _env().from_string(fail).render(
        dify_docker_dsl_name="example-app",
        dify_docker_agent_issuer_env_names=load_defaults()["dify_docker_agent_issuer_env_names"])
    assert "example-app" in message
    assert "OPENBAO_APPROLE_DIFY_AGENT_SECRET_ID_ISSUER_ROLE_ID" in message
    assert "OPENBAO_APPROLE_DIFY_AGENT_SECRET_ID_ISSUER_SECRET_ID" in message
    assert "dify_docker_agent_issuer_role_id" not in fail and "dify_docker_agent_issuer_secret_id" not in fail


def test_the_login_uses_the_declared_mount_and_variables_is_no_log_check_mode_safe_and_never_revokes():
    login = _task(_yaml(ISSUER), "Log in with the issuer AppRole")
    args = login["community.hashi_vault.vault_login"]
    assert args["auth_method"] == "approle"
    assert args["mount_point"] == "{{ dify_docker_agent_issuer_auth_mount }}"
    assert args["role_id"] == "{{ dify_docker_agent_issuer_role_id }}"
    assert args["secret_id"] == "{{ dify_docker_agent_issuer_secret_id }}"
    assert login["check_mode"] is False
    assert login["failed_when"] is False
    assert login.get("no_log") is True
    assert "revoke" not in str(_yaml(ISSUER)).lower()


def test_the_issuer_names_are_declared_defaults_and_appear_in_no_task():
    defaults = load_defaults()
    assert defaults["dify_docker_agent_issuer_env_names"] == {
        "role_id": "OPENBAO_APPROLE_DIFY_AGENT_SECRET_ID_ISSUER_ROLE_ID",
        "secret_id": "OPENBAO_APPROLE_DIFY_AGENT_SECRET_ID_ISSUER_SECRET_ID"}
    assert defaults["dify_docker_agent_issuer_auth_mount"] == "approle"
    for path in EDITED:
        text = path.read_text(encoding="utf-8")
        assert "OPENBAO_APPROLE" not in text and "dify-agent-secret-id-issuer" not in text, path.name


def test_the_five_approle_calls_use_the_issuer_token_and_the_mount_variable():
    calls = [task for path in (ONE, APPLY, PRUNE) for task in _yaml(path)
             if "dify_docker_agent_issuer_auth_mount" in task.get("ansible.builtin.uri", {}).get("url", "")]
    assert len(calls) == 5
    for task in calls:
        assert task["ansible.builtin.uri"]["headers"]["X-Vault-Token"] == (
            "{{ dify_docker_dsl_issuer_token }}"), task["name"]
    for path in EDITED:
        text = path.read_text(encoding="utf-8")
        assert "converge_writer_token" not in text and "/v1/auth/approle" not in text, path.name


def test_the_login_and_the_role_id_read_are_not_skipped_in_check_mode():
    assert _task(_yaml(ISSUER), "Log in with the issuer AppRole")["check_mode"] is False
    assert _task(_yaml(ONE), "Read the declared AppRole role id")["check_mode"] is False


def test_refused_login_or_empty_token_fails_with_no_response_detail_and_the_token_is_cleared():
    facts = _task(_yaml(ISSUER), "Keep the issuer token for this app")["ansible.builtin.set_fact"]
    for login, token, logged in (
        ({}, "", "False"),
        ({"login": {}}, "", "False"),
        ({"login": {"auth": {"client_token": ""}}}, "", "False"),
        ({"login": {"auth": {"client_token": "tok"}}}, "tok", "True"),
    ):
        context = {"dify_docker_dsl_issuer_login": login}
        assert _env().from_string(facts["dify_docker_dsl_issuer_token"]).render(**context).strip() == token
        assert _env().from_string(facts["dify_docker_dsl_issuer_logged"]).render(**context).strip() == logged
    fail = _task(_yaml(ISSUER), "Assert the issuer AppRole login returned a token")["ansible.builtin.assert"]["fail_msg"]
    assert "No response detail is shown" in fail and "dify_docker_dsl_issuer" not in fail
    clear = _task(_yaml(IMPORT_ONE), CLEAR)["ansible.builtin.set_fact"]
    assert clear["dify_docker_dsl_issuer_token"] == ""
    assert clear["dify_docker_dsl_issuer_logged"] is False
    assert clear["dify_docker_dsl_issuer_login"] == {}
