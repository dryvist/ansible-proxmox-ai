"""The Dify secret path is a role variable that defaults to the production path."""

from pathlib import Path
import re

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
TASKS = ROOT / "roles/dify_docker/tasks/storage_and_secret.yml"
CONFIG_TASKS = ROOT / "roles/dify_docker/tasks/config_and_deploy.yml"
COMPOSE = ROOT / "roles/dify_docker/templates/docker-compose.yml.j2"
SEED_TASK = "Seed the Dify SECRET_KEY through the shared seed_generated task"
RESOLVE_EVAL_TASK = "Resolve evaluation Dify secrets from OpenBao"
EVAL_GROUP = ROOT / "inventory/group_vars/dify_eval_group.yml"
ENV_LOOKUP = re.compile(r"lookup\('env', '([A-Z0-9_]+)'\)")
AGENT_SECRET_FIELDS = {
    "DIFY_AGENT_API_TOKEN": (
        "dify_docker_agent_api_token",
        "DIFY_AGENT_API_TOKEN",
        "DIFY_EVAL_AGENT_API_TOKEN",
    ),
    "DIFY_AGENT_SERVER_SECRET_KEY": (
        "dify_docker_agent_server_secret_key",
        "DIFY_AGENT_SERVER_SECRET_KEY",
        "DIFY_EVAL_AGENT_SERVER_SECRET_KEY",
    ),
    "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN": (
        "dify_docker_agent_local_sandbox_auth_token",
        "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN",
        "DIFY_EVAL_AGENT_LOCAL_SANDBOX_AUTH_TOKEN",
    ),
}


def render(expression, variables):
    templar = Templar(loader=DataLoader())
    templar.available_variables = {
        key: trust_as_template(value) if isinstance(value, str) else value
        for key, value in variables.items()
    }
    return templar.template(trust_as_template(expression))


def eval_seed_inputs(eval_group):
    for variable_name, expression in eval_group.items():
        env_names = ENV_LOOKUP.findall(str(expression))
        if env_names and env_names[0].startswith("DIFY_EVAL_") and "or undef(" not in str(expression):
            yield env_names[0].replace("DIFY_EVAL_", "DIFY_", 1), variable_name, env_names[0]


def test_default_path_is_the_production_path():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))

    assert defaults["dify_docker_openbao_path"] == "ai/dify"


def test_seed_task_reads_the_path_variable():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    seed = next(task for task in tasks if task.get("name") == SEED_TASK)
    seed_vars = seed["block"][0]["vars"]

    assert seed_vars["openbao_secrets_seed_path"] == "{{ dify_docker_openbao_path }}"


def test_eval_path_keeps_seeded_secrets_under_the_eval_namespace():
    eval_group = yaml.safe_load(EVAL_GROUP.read_text(encoding="utf-8"))

    assert eval_group["dify_docker_openbao_path"].startswith("ai/eval/")


def test_agent_runtime_secrets_are_generated_and_seeded():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
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

    for field_name, (var_name, env_name, eval_env_name) in AGENT_SECRET_FIELDS.items():
        assert f"lookup('env', '{env_name}')" in defaults[var_name]
        assert f"lookup('env', '{eval_env_name}')" in defaults[var_name]
        assert "dify_eval_group" in defaults[var_name]
        assert fields[field_name] == {
            "name": field_name,
            "length": 50,
            "charset": "alnum",
            "value": variables[var_name],
        }

    assert seed_vars["openbao_secrets_seed_path"] == "{{ dify_docker_openbao_path }}"


@pytest.mark.parametrize("field", AGENT_SECRET_FIELDS.values())
def test_eval_agent_secrets_use_only_the_eval_environment(field, monkeypatch):
    var_name, prod_env, eval_env = field
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    templar = Templar(loader=DataLoader())
    templar.available_variables = {"group_names": ["dify_eval_group"]}
    monkeypatch.setenv(prod_env, "production-marker")
    monkeypatch.setenv(eval_env, "evaluation-marker")

    expression = trust_as_template(defaults[var_name])
    assert templar.template(expression) == "evaluation-marker"
    monkeypatch.delenv(eval_env)
    assert templar.template(expression) == ""
    templar.available_variables = {"group_names": []}
    assert templar.template(expression) == "production-marker"


def test_eval_seeded_secrets_render_from_the_eval_path_and_stored_values(monkeypatch):
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    eval_group = yaml.safe_load(EVAL_GROUP.read_text(encoding="utf-8"))
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    seed = next(task for task in tasks if task.get("name") == SEED_TASK)
    seed_vars = seed["block"][0]["vars"]

    seed_inputs = list(eval_seed_inputs(eval_group))
    for _, _, eval_env in seed_inputs:
        monkeypatch.setenv(eval_env, "evaluation-caller-marker")
        monkeypatch.setenv(eval_env.replace("DIFY_EVAL_", "DIFY_"), "production-marker")
    for env_name in (
        "DIFY_EVAL_AGENT_API_TOKEN",
        "DIFY_EVAL_AGENT_SERVER_SECRET_KEY",
        "DIFY_EVAL_AGENT_LOCAL_SANDBOX_AUTH_TOKEN",
    ):
        monkeypatch.setenv(env_name, "evaluation-agent-marker")
    monkeypatch.setenv("DIFY_EVAL_ADMIN_EMAIL", "eval-admin@example.invalid")
    monkeypatch.setenv("DIFY_EVAL_MODEL_API_KEY", "evaluation-model-marker")
    monkeypatch.setenv("PROXMOX_SUBDOMAIN", "pve.example.test")

    variables = {
        **defaults,
        **eval_group,
        "group_names": ["dify_eval_group"],
        "bao_local_llm_secrets": {},
        "bao_apps_secrets": {},
        "dify_docker_secret_key_caller": "",
    }
    fields = render(seed_vars["openbao_secrets_seed_fields"], variables)
    field_by_name = {field["name"]: field for field in fields}
    assert render(seed_vars["openbao_secrets_seed_path"], variables) == eval_group[
        "dify_docker_openbao_path"
    ]
    for field_name, variable_name, _ in seed_inputs:
        assert field_name in field_by_name
        assert field_by_name[field_name]["value"] == "evaluation-caller-marker"
        assert render("{{ " + variable_name + " }}", variables) == "evaluation-caller-marker"

    stored = {field["name"]: "openbao-" + field["name"].lower() for field in fields}
    variables["dify_docker_seed_result"] = stored
    resolve = next(task for task in tasks if task.get("name") == RESOLVE_EVAL_TASK)
    for variable_name, expression in resolve["ansible.builtin.set_fact"].items():
        variables[variable_name] = render(expression, variables)
    variables.update(
        {
            "dify_docker_secret_key": stored["DIFY_SECRET_KEY"],
            "dify_docker_agent_api_token": stored["DIFY_AGENT_API_TOKEN"],
            "dify_docker_agent_server_secret_key": stored["DIFY_AGENT_SERVER_SECRET_KEY"],
            "dify_docker_agent_local_sandbox_auth_token": stored[
                "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN"
            ],
            "ai_agent_default_concurrency": 2,
            "ai_orchestration_otel_endpoint": "http://otel.example.invalid:4318",
            "dify_docker_web_port": "38080",
            "dify_docker_postgres_port": "35432",
            "dify_docker_redis_port": "36379",
            "dify_docker_model_endpoint_url": "http://llm.example.invalid/v1",
        }
    )
    rendered = render(COMPOSE.read_text(encoding="utf-8"), variables)
    compose = yaml.safe_load(rendered)

    assert (
        compose["services"]["api"]["environment"]["DB_PASSWORD"]
        == stored["DIFY_DB_PASSWORD"]
    )
    assert (
        compose["services"]["db"]["environment"]["POSTGRES_PASSWORD"]
        == stored["DIFY_DB_PASSWORD"]
    )
    assert (
        compose["services"]["api"]["environment"]["REDIS_PASSWORD"]
        == stored["DIFY_REDIS_PASSWORD"]
    )
    assert stored["DIFY_REDIS_PASSWORD"] in compose["services"]["redis"]["command"]


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
