"""The owner email discovery, adoption and fail-closed gates for the Dify admin.

Discovery runs only when the leaf and the environment carry no email. The
adoption, setup-state check and assert tasks decide whether the converge may
continue.
"""

from _admin_secret_shared import (
    ADOPT_TASK,
    DEFAULTS,
    DISCOVER_TASK,
    EMAIL_ASSERT_TASK,
    EMPTY_RESULT,
    FAIL_CLOSED_TASK,
    FAILED_LOOKUP,
    FINISHED_SETUP,
    LEAF_EMAIL,
    NOT_STARTED_SETUP,
    OWNER_EMAIL,
    SEED_TASK,
    SETUP_CHECK_TASK,
    TASKS,
    UNANSWERED_SETUP,
    email_gate_context,
    load_yaml,
    render,
    render_all,
    task_named,
)


def test_owner_email_discovery_is_no_log_and_gated_on_a_leaf_without_the_email(
    monkeypatch
):
    task = task_named(DISCOVER_TASK)

    assert task["no_log"] is True
    assert task["changed_when"] is False
    assert task["ansible.builtin.command"]["chdir"] == "{{ dify_docker_data_dir }}"
    cmd = task["ansible.builtin.command"]["cmd"]
    assert "docker compose exec -T db psql" in cmd
    assert "dify_docker_db_user" in cmd
    assert "dify_docker_db_name" in cmd
    assert "tenant_account_joins" in cmd

    # With the leaf and the environment both empty the gate must open whatever
    # the query returned: the failed run of a not-yet-set-up instance and the
    # empty answer both belong to the fail-closed logic downstream, not here.
    for setup_state in (NOT_STARTED_SETUP, FINISHED_SETUP):
        for query in (FAILED_LOOKUP, EMPTY_RESULT):
            variables = email_gate_context(monkeypatch, query, setup_state)
            assert all(render_all(task["when"], variables))

    eval_variables = email_gate_context(monkeypatch, EMPTY_RESULT, NOT_STARTED_SETUP)
    eval_variables["group_names"] = ["dify_eval_group"]
    assert not all(render_all(task["when"], eval_variables))


def test_adopted_owner_email_is_no_log_and_only_from_a_successful_query(monkeypatch):
    task = task_named(ADOPT_TASK)

    assert task["no_log"] is True
    assert task["ansible.builtin.set_fact"] == {
        "dify_docker_admin_email_discovered": "{{ dify_docker_owner_email_query.stdout | trim }}"
    }

    # A failed query and a query that answered but found no owner both skip
    # the adoption, on a fresh install and on an already-set-up instance
    # alike; only a successful query carrying an email adopts it.
    for setup_state in (NOT_STARTED_SETUP, FINISHED_SETUP):
        for query in (FAILED_LOOKUP, EMPTY_RESULT):
            variables = email_gate_context(monkeypatch, query, setup_state)
            assert not all(render_all(task["when"], variables))

    success = email_gate_context(
        monkeypatch, {"rc": 0, "stdout": OWNER_EMAIL}, FINISHED_SETUP
    )
    assert all(render_all(task["when"], success))


def test_setup_check_runs_whenever_no_email_was_found(monkeypatch):
    task = task_named(SETUP_CHECK_TASK)

    assert task["failed_when"] is False
    assert "dify_docker_console_api_base" in task["ansible.builtin.uri"]["url"]
    for query in (FAILED_LOOKUP, EMPTY_RESULT):
        variables = email_gate_context(monkeypatch, query, {})
        assert all(render_all(task["when"], variables))


def test_setup_check_does_not_run_when_an_email_was_found(monkeypatch):
    task = task_named(SETUP_CHECK_TASK)
    defaults = load_yaml(DEFAULTS)

    monkeypatch.setenv("DIFY_ADMIN_EMAIL", OWNER_EMAIL)
    with_env = {
        **defaults,
        "bao_local_llm_secrets": {},
        "group_names": [],
        "dify_docker_owner_email_query": EMPTY_RESULT,
    }
    assert not all(render_all(task["when"], with_env))

    with_leaf = {
        **defaults,
        "bao_local_llm_secrets": {"DIFY_ADMIN_EMAIL": LEAF_EMAIL},
        "group_names": [],
        "dify_docker_owner_email_query": FAILED_LOOKUP,
    }
    assert not all(render_all(task["when"], with_leaf))


def test_setup_finished_without_an_owner_fails_closed(monkeypatch):
    # Both an empty discovery result and a failed discovery on a finished
    # setup must reach the assert and stop the converge.
    names = [task.get("name") for task in load_yaml(TASKS)]
    assert names.index(DISCOVER_TASK) < names.index(SETUP_CHECK_TASK)
    assert names.index(SETUP_CHECK_TASK) < names.index(FAIL_CLOSED_TASK)
    assert names.index(FAIL_CLOSED_TASK) < names.index(EMAIL_ASSERT_TASK)
    assert names.index(EMAIL_ASSERT_TASK) < names.index(SEED_TASK)
    assert names.index(ADOPT_TASK) < names.index(SEED_TASK)

    fail_closed = task_named(FAIL_CLOSED_TASK)
    # The assert's that expression and fail_msg carry no secret-bearing
    # variable, so its output is not redacted.
    assert "no_log" not in fail_closed
    assert fail_closed["ansible.builtin.assert"]["quiet"] is True
    assert "DIFY_ADMIN_EMAIL" in fail_closed["ansible.builtin.assert"]["fail_msg"]

    for query in (EMPTY_RESULT, FAILED_LOOKUP):
        variables = email_gate_context(monkeypatch, query, FINISHED_SETUP)
        assert all(render_all(fail_closed["when"], variables))
        assert not all(
            render_all(fail_closed["ansible.builtin.assert"]["that"], variables)
        )


def test_unanswered_setup_check_without_an_owner_fails_closed(monkeypatch):
    variables = email_gate_context(monkeypatch, FAILED_LOOKUP, UNANSWERED_SETUP)
    fail_closed = task_named(FAIL_CLOSED_TASK)

    assert all(render_all(fail_closed["when"], variables))
    assert not all(
        render_all(fail_closed["ansible.builtin.assert"]["that"], variables)
    )


def test_not_started_setup_requires_a_non_empty_admin_email(monkeypatch):
    task = task_named(EMAIL_ASSERT_TASK)
    assert task["ansible.builtin.assert"]["quiet"] is True
    assert "DIFY_ADMIN_EMAIL" in task["ansible.builtin.assert"]["fail_msg"]

    empty_email = email_gate_context(monkeypatch, FAILED_LOOKUP, NOT_STARTED_SETUP)
    assert all(render_all(task["when"], empty_email))
    assert not all(
        render_all(task["ansible.builtin.assert"]["that"], empty_email)
    )

    discovered_email = email_gate_context(
        monkeypatch, FAILED_LOOKUP, NOT_STARTED_SETUP
    )
    discovered_email["dify_docker_admin_email_discovered"] = OWNER_EMAIL
    assert all(
        render_all(task["ansible.builtin.assert"]["that"], discovered_email)
    )


def test_fresh_install_without_an_owner_email_fails_closed(monkeypatch):
    # A stack that is not up yet (first run) makes the discovery query fail
    # (rc != 0) and leaves the setup-state probe unanswered. The probe still
    # runs (no email was found anywhere) and its failed_when keeps the play
    # alive until the fail-closed assert decides; with no configured email
    # anywhere the converge must stop instead of seeding or posting an empty
    # admin email.
    variables = email_gate_context(monkeypatch, FAILED_LOOKUP, UNANSWERED_SETUP)
    assert render("{{ dify_docker_admin_email }}", variables) == ""

    setup_check = task_named(SETUP_CHECK_TASK)
    assert all(render_all(setup_check["when"], variables))
    assert setup_check["failed_when"] is False

    fail_closed = task_named(FAIL_CLOSED_TASK)
    assert all(render_all(fail_closed["when"], variables))
    assert not all(
        render_all(fail_closed["ansible.builtin.assert"]["that"], variables)
    )
