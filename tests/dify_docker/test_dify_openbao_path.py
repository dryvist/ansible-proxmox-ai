"""The Dify secret path is a role variable that defaults to the production path."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
TASKS = ROOT / "roles/dify_docker/tasks/storage_and_secret.yml"
SEED_TASK = "Seed Dify and Agent runtime secrets through the shared seed_generated task"
EVAL_GROUP = ROOT / "inventory/group_vars/dify_eval_group.yml"
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
    fields = {field["name"]: field for field in seed_vars["openbao_secrets_seed_fields"]}

    for field_name, (var_name, env_name) in AGENT_SECRET_FIELDS.items():
        assert defaults[var_name] == f"{{{{ lookup('env', '{env_name}') }}}}"
        assert fields[field_name] == {
            "name": field_name,
            "length": 50,
            "charset": "alnum",
            "value": f"{{{{ {var_name} }}}}",
        }

    assert seed_vars["openbao_secrets_seed_path"] == "{{ dify_docker_openbao_path }}"
