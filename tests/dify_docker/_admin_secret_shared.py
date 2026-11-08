"""Shared constants and helpers for the Dify admin credential tests.

The helpers read storage_and_secret.yml, the Dify defaults and the inventory
group vars as data and render their Jinja expressions with Ansible's templar,
so the tests assert against the role's own expressions.
"""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _defaults import DEFAULTS, load_defaults

ROOT =Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/dify_docker/tasks/storage_and_secret.yml"
GROUP_VARS = ROOT / "inventory/group_vars/dify_group.yml"
SEED_TASK = "Seed the Dify SECRET_KEY through the shared seed_generated task"
DISCOVER_TASK = "Discover the Dify owner email on the guest when the leaf has none"
SETUP_CHECK_TASK = "Check whether the Dify setup is finished when no owner email was found"
FAIL_CLOSED_TASK = "Require a confirmed fresh setup when no owner email was found"
EMAIL_ASSERT_TASK = "Require a non-empty Dify admin email on a not-yet-set-up instance"
ADOPT_TASK = "Adopt the discovered owner email as the Dify admin email"
CONSOLE_BASE_ASSERT_TASK = "Require the Dify console API base to be set"
READ_BACK_TASK = "Resolve the Dify admin credentials from the secret store"

LEAF_EMAIL = "openbao-leaf-email"
LEAF_PASSWORD = "openbao-leaf-password"
OWNER_EMAIL = "owner-email-marker"


def load_yaml(path):
    if path.is_dir():
        return load_defaults()
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
    defaults = load_defaults()
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
