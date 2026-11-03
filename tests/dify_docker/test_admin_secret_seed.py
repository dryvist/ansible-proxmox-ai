"""The Dify admin seed carries the password and email and keeps stored values.

The seed fields are rendered from storage_and_secret.yml. Evaluation hosts seed
only the password, never the admin email.
"""

from _admin_secret_shared import (
    DEFAULTS,
    DIFY_EVAL_ENVS,
    EVAL_EMAIL,
    EVAL_GROUP,
    EVAL_PASSWORD,
    LEAF_EMAIL,
    LEAF_PASSWORD,
    OWNER_EMAIL,
    SEED_TASK,
    load_yaml,
    render,
    seed_fields,
    task_named,
)


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


def test_production_seed_without_any_email_has_no_admin_email_field(monkeypatch):
    # No leaf email, no environment email and no discovered email: the seed
    # conditional must leave DIFY_ADMIN_EMAIL out of the seed fields entirely
    # instead of seeding an empty value.
    defaults = load_yaml(DEFAULTS)
    monkeypatch.delenv("DIFY_ADMIN_EMAIL", raising=False)
    variables = {
        **defaults,
        "bao_local_llm_secrets": {},
        "dify_docker_admin_email_discovered": "",
    }

    fields = seed_fields([], variables)

    assert "DIFY_ADMIN_EMAIL" not in fields


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


def test_seed_task_sets_no_log():
    assert task_named(SEED_TASK).get("no_log") is True
