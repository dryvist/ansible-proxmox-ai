"""Production and evaluation Dify admin credentials resolve from the leaf.

Each host reads DIFY_ADMIN_EMAIL and DIFY_ADMIN_PASSWORD from the seeded
OpenBao leaf or the environment; evaluation hosts read only DIFY_EVAL_* names.
"""

from _admin_secret_shared import (
    DEFAULTS,
    EVAL_EMAIL,
    EVAL_GROUP,
    EVAL_PASSWORD,
    EVAL_RESOLVE_TASK,
    LEAF_EMAIL,
    LEAF_PASSWORD,
    OWNER_EMAIL,
    READ_BACK_TASK,
    load_yaml,
    render,
    task_named,
)


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
