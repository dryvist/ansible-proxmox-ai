"""Production Dify admin credentials resolve from the seeded OpenBao leaf.

The seed fields in storage_and_secret.yml carry DIFY_ADMIN_PASSWORD for every
Dify host (stored values kept, generated once when absent) and, on production
hosts whose leaf lacks DIFY_ADMIN_EMAIL, the email discovered from the existing
owner account on the guest. Evaluation hosts keep reading only DIFY_EVAL_*
names and are never queried or seeded with production values.
"""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
TASKS = ROOT / "roles/dify_docker/tasks/storage_and_secret.yml"
EVAL_GROUP = ROOT / "inventory/group_vars/dify_eval_group.yml"
SEED_TASK = "Seed the Dify SECRET_KEY through the shared seed_generated task"
DISCOVER_TASK = "Discover the Dify owner email on the guest when the leaf has none"
SETUP_CHECK_TASK = "Check whether the Dify setup is finished when no owner email was found"
FAIL_CLOSED_TASK = "Require a confirmed fresh setup when no owner email was found"
EMAIL_ASSERT_TASK = "Require a non-empty Dify admin email on a not-yet-set-up instance"
ADOPT_TASK = "Adopt the discovered owner email as the Dify admin email"
READ_BACK_TASK = "Resolve the Dify admin credentials from the secret store"
EVAL_RESOLVE_TASK = "Resolve evaluation Dify secrets from OpenBao"

LEAF_EMAIL = "openbao-leaf-email"
LEAF_PASSWORD = "openbao-leaf-password"
OWNER_EMAIL = "owner-email-marker"
EVAL_EMAIL = "evaluation-email-marker"
EVAL_PASSWORD = "evaluation-password-marker"

DIFY_EVAL_ENVS = [
    "DIFY_EVAL_DB_PASSWORD",
    "DIFY_EVAL_REDIS_PASSWORD",
    "DIFY_EVAL_SANDBOX_API_KEY",
    "DIFY_EVAL_WEAVIATE_API_KEY",
    "DIFY_EVAL_PLUGIN_DAEMON_KEY",
    "DIFY_EVAL_INNER_API_KEY",
    "DIFY_EVAL_AGENT_API_TOKEN",
    "DIFY_EVAL_AGENT_SERVER_SECRET_KEY",
    "DIFY_EVAL_AGENT_LOCAL_SANDBOX_AUTH_TOKEN",
]


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def render(expression, variables):
    templar = Templar(loader=DataLoader())
    templar.available_variables = {
        key: trust_as_template(value) if isinstance(value, str) else value
        for key, value in variables.items()
    }
    return templar.template(trust_as_template(expression))


def task_named(name):
    tasks = load_yaml(TASKS)
    return next(task for task in tasks if task.get("name") == name)


def seed_fields(group_names, extra_variables):
    seed_vars = task_named(SEED_TASK)["block"][0]["vars"]
    context = {
        "dify_docker_secret_key_caller": "",
        **extra_variables,
        "group_names": group_names,
    }
    return {
        field["name"]: field
        for field in render(seed_vars["openbao_secrets_seed_fields"], context)
    }


# A discovery query whose exec failed (stack not up yet) with failed_when:
# false, and one that succeeded but found no owner: neither leaves an email.
FAILED_LOOKUP = {"rc": 1, "stdout": ""}
EMPTY_RESULT = {"rc": 0, "stdout": ""}
# The setup-state probe: answered not_started, answered finished, or never
# answered (no json — stack down or connection refused).
NOT_STARTED_SETUP = {"json": {"step": "not_started"}}
FINISHED_SETUP = {"json": {"step": "finished"}}
UNANSWERED_SETUP = {"status": -1}


def email_gate_context(monkeypatch, query, setup_state):
    defaults = load_yaml(DEFAULTS)
    monkeypatch.delenv("DIFY_ADMIN_EMAIL", raising=False)
    return {
        **defaults,
        "bao_local_llm_secrets": {},
        "group_names": [],
        "dify_docker_owner_email_query": query,
        "dify_docker_owner_setup_state": setup_state,
    }


def render_all(clauses, variables):
    return [render("{{ " + clause + " }}", variables) for clause in clauses]


def test_production_admin_credentials_resolve_from_the_leaf(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    monkeypatch.delenv("DIFY_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("DIFY_ADMIN_PASSWORD", raising=False)
    variables = {
        **defaults,
        "bao_local_llm_secrets": {
            "DIFY_ADMIN_EMAIL": LEAF_EMAIL,
            "DIFY_ADMIN_PASSWORD": LEAF_PASSWORD,
        },
    }

    assert render("{{ dify_docker_admin_email }}", variables) == LEAF_EMAIL
    assert render("{{ dify_docker_admin_password }}", variables) == LEAF_PASSWORD


def test_production_admin_credentials_fall_back_to_the_environment(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    monkeypatch.setenv("DIFY_ADMIN_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("DIFY_ADMIN_PASSWORD", LEAF_PASSWORD)
    variables = {**defaults, "bao_local_llm_secrets": {}}

    assert render("{{ dify_docker_admin_email }}", variables) == OWNER_EMAIL
    assert render("{{ dify_docker_admin_password }}", variables) == LEAF_PASSWORD


def test_discovered_owner_email_reaches_the_admin_email(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    monkeypatch.delenv("DIFY_ADMIN_EMAIL", raising=False)
    variables = {
        **defaults,
        "bao_local_llm_secrets": {},
        "dify_docker_admin_email_discovered": OWNER_EMAIL,
    }

    assert render("{{ dify_docker_admin_email }}", variables) == OWNER_EMAIL


def test_production_seed_carries_the_admin_password_and_email(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    monkeypatch.delenv("DIFY_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("DIFY_ADMIN_PASSWORD", raising=False)
    variables = {
        **defaults,
        "bao_local_llm_secrets": {},
        "dify_docker_admin_email_discovered": OWNER_EMAIL,
    }

    fields = seed_fields([], variables)

    assert fields["DIFY_ADMIN_PASSWORD"] == {
        "name": "DIFY_ADMIN_PASSWORD",
        "length": 50,
        "charset": "alnum",
        "value": "",
    }
    assert fields["DIFY_ADMIN_EMAIL"]["value"] == OWNER_EMAIL


def test_production_seed_keeps_the_stored_leaf_values(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    monkeypatch.delenv("DIFY_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("DIFY_ADMIN_PASSWORD", raising=False)
    variables = {
        **defaults,
        "bao_local_llm_secrets": {
            "DIFY_ADMIN_EMAIL": LEAF_EMAIL,
            "DIFY_ADMIN_PASSWORD": LEAF_PASSWORD,
        },
    }

    fields = seed_fields([], variables)

    assert fields["DIFY_ADMIN_PASSWORD"]["value"] == LEAF_PASSWORD
    assert fields["DIFY_ADMIN_EMAIL"]["value"] == LEAF_EMAIL


def test_evaluation_group_reads_only_its_own_names(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    eval_group = load_yaml(EVAL_GROUP)
    monkeypatch.setenv("DIFY_ADMIN_EMAIL", OWNER_EMAIL)
    monkeypatch.setenv("DIFY_ADMIN_PASSWORD", LEAF_PASSWORD)
    monkeypatch.setenv("DIFY_EVAL_ADMIN_EMAIL", EVAL_EMAIL)
    monkeypatch.setenv("DIFY_EVAL_ADMIN_PASSWORD", EVAL_PASSWORD)
    variables = {
        **defaults,
        "bao_local_llm_secrets": {
            "DIFY_ADMIN_EMAIL": LEAF_EMAIL,
            "DIFY_ADMIN_PASSWORD": LEAF_PASSWORD,
        },
        **eval_group,
    }

    assert render("{{ dify_docker_admin_email }}", variables) == EVAL_EMAIL
    assert render("{{ dify_docker_admin_password }}", variables) == EVAL_PASSWORD


def test_evaluation_seed_carries_the_admin_password_but_no_email(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    eval_group = load_yaml(EVAL_GROUP)
    for env_name in DIFY_EVAL_ENVS:
        monkeypatch.setenv(env_name, EVAL_PASSWORD)
    monkeypatch.setenv("DIFY_EVAL_ADMIN_EMAIL", EVAL_EMAIL)
    monkeypatch.setenv("DIFY_EVAL_ADMIN_PASSWORD", EVAL_PASSWORD)
    monkeypatch.setenv("DIFY_ADMIN_EMAIL", OWNER_EMAIL)
    variables = {
        **defaults,
        "bao_local_llm_secrets": {
            "DIFY_ADMIN_EMAIL": LEAF_EMAIL,
            "DIFY_ADMIN_PASSWORD": LEAF_PASSWORD,
        },
        **eval_group,
    }

    fields = seed_fields(["dify_eval_group"], variables)

    assert fields["DIFY_ADMIN_PASSWORD"]["value"] == EVAL_PASSWORD
    assert "DIFY_ADMIN_EMAIL" not in fields
    seed_vars = task_named(SEED_TASK)["block"][0]["vars"]
    assert render(seed_vars["openbao_secrets_seed_path"], variables) == eval_group[
        "dify_docker_openbao_path"
    ]


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

    fail_closed = task_named(FAIL_CLOSED_TASK)
    assert fail_closed["no_log"] is True
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


def test_seed_read_back_resolves_the_admin_credentials_for_every_host():
    task = task_named(READ_BACK_TASK)

    assert task["no_log"] is True
    assert "when" not in task
    set_fact = task["ansible.builtin.set_fact"]
    assert set_fact["dify_docker_admin_password"] == (
        "{{ dify_docker_seed_result.DIFY_ADMIN_PASSWORD }}"
    )
    assert "dify_docker_seed_result['DIFY_ADMIN_EMAIL']" in set_fact["dify_docker_admin_email"]

    stored = {
        "dify_docker_seed_result": {
            "DIFY_ADMIN_PASSWORD": LEAF_PASSWORD,
            "DIFY_ADMIN_EMAIL": LEAF_EMAIL,
        },
        "dify_docker_admin_email": OWNER_EMAIL,
    }
    assert render(set_fact["dify_docker_admin_password"], stored) == LEAF_PASSWORD
    assert render(set_fact["dify_docker_admin_email"], stored) == LEAF_EMAIL

    email_missing = {
        "dify_docker_seed_result": {"DIFY_ADMIN_PASSWORD": LEAF_PASSWORD},
        "dify_docker_admin_email": OWNER_EMAIL,
    }
    assert render(set_fact["dify_docker_admin_email"], email_missing) == OWNER_EMAIL


def test_the_evaluation_resolve_no_longer_duplicates_the_admin_password():
    assert "dify_docker_admin_password" not in task_named(EVAL_RESOLVE_TASK)[
        "ansible.builtin.set_fact"
    ]


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
