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
SETUP_CHECK_TASK = "Check whether the Dify setup is finished when no owner was found"
FAIL_CLOSED_TASK = "Require an owner email when the Dify setup is already finished"
ADOPT_TASK = "Adopt the discovered owner email as the Dify admin email"
READ_BACK_TASK = "Resolve the Dify admin credentials from the secret store"
EVAL_RESOLVE_TASK = "Resolve evaluation Dify secrets from OpenBao"

LEAF_EMAIL = "openbao-leaf-email"
LEAF_PASSWORD = "openbao-leaf-password"
OWNER_EMAIL = "owner@example.invalid"
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


def test_owner_email_discovery_is_no_log_and_gated_on_a_leaf_without_the_email():
    task = task_named(DISCOVER_TASK)

    assert task["no_log"] is True
    assert task["changed_when"] is False
    assert task["ansible.builtin.command"]["chdir"] == "{{ dify_docker_data_dir }}"
    cmd = task["ansible.builtin.command"]["cmd"]
    assert "docker compose exec -T db psql" in cmd
    assert "dify_docker_db_user" in cmd
    assert "dify_docker_db_name" in cmd
    assert "tenant_account_joins" in cmd
    when = task["when"]
    assert "'dify_eval_group' not in (group_names | default([]))" in when
    leaf_clauses = [
        clause
        for clause in when
        if "bao_local_llm_secrets" in clause and "DIFY_ADMIN_EMAIL" in clause
    ]
    assert leaf_clauses and "length == 0" in leaf_clauses[0]
    assert any(
        "lookup('env', 'DIFY_ADMIN_EMAIL')" in clause and "length == 0" in clause
        for clause in when
    )


def test_adopted_owner_email_is_no_log_and_only_from_a_successful_query():
    task = task_named(ADOPT_TASK)

    assert task["no_log"] is True
    assert task["ansible.builtin.set_fact"] == {
        "dify_docker_admin_email_discovered": "{{ dify_docker_owner_email_query.stdout | trim }}"
    }
    when = task["when"]
    assert "'dify_eval_group' not in (group_names | default([]))" in when
    assert any("rc | default(-1) | int == 0" in clause for clause in when)
    assert any("trim | length > 0" in clause for clause in when)


def test_setup_finished_without_an_owner_fails_closed():
    tasks = load_yaml(TASKS)
    names = [task.get("name") for task in tasks]
    discovery_index = names.index(DISCOVER_TASK)
    setup_check_index = names.index(SETUP_CHECK_TASK)
    fail_closed = task_named(FAIL_CLOSED_TASK)
    fail_closed_index = names.index(FAIL_CLOSED_TASK)

    assert discovery_index < setup_check_index < fail_closed_index < names.index(SEED_TASK)
    assert fail_closed["no_log"] is True
    assert fail_closed["ansible.builtin.assert"]["quiet"] is True
    assert "DIFY_ADMIN_EMAIL" in fail_closed["ansible.builtin.assert"]["fail_msg"]
    that = fail_closed["ansible.builtin.assert"]["that"]
    assert any("step" in clause and "not_started" in clause for clause in that)
    when = fail_closed["when"]
    assert any("rc | default(-1) | int == 0" in clause for clause in when)
    assert any("trim | length == 0" in clause for clause in when)


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


def test_fresh_install_without_an_owner_falls_back_instead_of_failing(monkeypatch):
    defaults = load_yaml(DEFAULTS)
    monkeypatch.delenv("DIFY_ADMIN_EMAIL", raising=False)

    # A stack that is not up yet (first run) makes the discovery query fail
    # (rc != 0), so neither the setup check nor the fail-closed assert runs,
    # and the default falls back to the caller value without raising.
    fail_closed_when = task_named(FAIL_CLOSED_TASK)["when"]
    assert any("rc | default(-1) | int == 0" in clause for clause in fail_closed_when)
    variables = {**defaults, "bao_local_llm_secrets": {}}
    assert render("{{ dify_docker_admin_email }}", variables) == ""
