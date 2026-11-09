"""The Dify secret path is a role variable that defaults to the production path."""

from pathlib import Path

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _defaults import load_defaults


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/dify_docker/tasks/storage_and_secret.yml"
CONFIG_TASKS = ROOT / "roles/dify_docker/tasks/config_and_deploy.yml"
SEED_TASK = "Seed the Dify SECRET_KEY through the shared seed_generated task"
AGENT_SECRET_FIELDS = {
    "DIFY_AGENT_API_TOKEN": (
        "dify_docker_agent_api_token",
        "DIFY_AGENT_API_TOKEN",
    ),
    "DIFY_AGENT_SERVER_SECRET_KEY": (
        "dify_docker_agent_server_secret_key",
        "DIFY_AGENT_SERVER_SECRET_KEY",
    ),
    "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN": (
        "dify_docker_agent_local_sandbox_auth_token",
        "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN",
    ),
}


def render(expression, variables):
    templar = Templar(loader=DataLoader())
    templar.available_variables = {
        key: trust_as_template(value) if isinstance(value, str) else value
        for key, value in variables.items()
    }
    return templar.template(trust_as_template(expression))


def test_default_path_is_the_production_path():
    defaults = load_defaults()

    assert defaults["dify_docker_openbao_path"] == "ai/dify"


def test_seed_task_reads_the_path_variable():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    seed = next(task for task in tasks if task.get("name") == SEED_TASK)
    seed_vars = seed["block"][0]["vars"]

    assert seed_vars["openbao_secrets_seed_path"] == "{{ dify_docker_openbao_path }}"


def test_agent_runtime_secrets_are_generated_and_seeded():
    defaults = load_defaults()
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    seed = next(task for task in tasks if task.get("name") == SEED_TASK)
    seed_vars = seed["block"][0]["vars"]
    variables = {
        **defaults,
        "group_names": [],
        "dify_docker_secret_key_caller": "fixture-secret-key",
        "dify_docker_agent_api_token": "fixture-agent-token",
        "dify_docker_agent_server_secret_key": "fixture-agent-key",
        "dify_docker_agent_local_sandbox_auth_token": "fixture-sandbox-token",
    }
    fields = {
        field["name"]: field
        for field in render(seed_vars["openbao_secrets_seed_fields"], variables)
    }

    for field_name, (var_name, env_name) in AGENT_SECRET_FIELDS.items():
        assert f"lookup('env', '{env_name}')" in defaults[var_name]
        if field_name == "DIFY_AGENT_SERVER_SECRET_KEY":
            # The Agent Stub key is generated in-role in the 43-character
            # unpadded base64url shape (see
            # test_agent_server_secret_key.py), and a stored value that fails
            # the valid_pattern is replaced by the shared seed task.
            assert fields[field_name] == {
                "name": field_name,
                "valid_pattern": "^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$",
                "value": variables[var_name],
            }
        else:
            assert fields[field_name] == {
                "name": field_name,
                "length": 50,
                "charset": "alnum",
                "value": variables[var_name],
            }

    assert seed_vars["openbao_secrets_seed_path"] == "{{ dify_docker_openbao_path }}"


@pytest.mark.parametrize("field", AGENT_SECRET_FIELDS.values())
def test_agent_secrets_read_the_production_environment(field, monkeypatch):
    var_name, prod_env = field
    defaults = load_defaults()
    templar = Templar(loader=DataLoader())
    templar.available_variables = {"group_names": []}
    monkeypatch.setenv(prod_env, "production-marker")

    assert templar.template(trust_as_template(defaults[var_name])) == "production-marker"


def test_secrets_assert_precedes_every_restart_notifier():
    tasks = yaml.safe_load(CONFIG_TASKS.read_text(encoding="utf-8"))
    assert_index = next(
        index
        for index, task in enumerate(tasks)
        if task.get("name") == "Assert every secret rendered into the Dify compose file is present"
    )
    notify_indices = [
        index
        for index, task in enumerate(tasks)
        if "Restart dify" in (task.get("notify") or [])
    ]

    assert notify_indices
    assert assert_index < min(notify_indices)
