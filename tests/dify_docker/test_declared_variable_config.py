"""Declared catalog app variables: the unchanged task sequence and the issuer login."""

from _agent_dsl_shared import IMPORT_ONE, _expanded_yaml
from _declared_variables_shared import (
    APPLY,
    CLEAR,
    DERIVE,
    EDITED,
    ISSUER,
    NEW_TOP_LEVEL,
    ONE,
    PRE,
    PRUNE,
    RESOLVE,
    _env,
    _fact,
    _task,
    _yaml,
)
from _defaults import load_defaults


# The import sequence before the declared-variable work. Apps without declared variables must still run it.
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
