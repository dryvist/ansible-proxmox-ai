"""Agent app API keys are created once and stored in per-app OpenBao leaves."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
IMPORT_TASKS = ROOT / "roles/dify_docker/tasks/import_agent_dsl.yml"
API_KEY_TASKS = ROOT / "roles/dify_docker/tasks/ensure_agent_app_api_key.yml"

READ_TASK = "Read the stored Dify agent app API key from OpenBao"
CREATE_TASK = "Create the Dify agent app API key only when OpenBao has none"
STORE_TASK = "Store the Dify agent app API key through the shared seed_generated task"


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_api_key_provisioning_is_wired_after_each_agent_dsl():
    tasks = _yaml(IMPORT_TASKS)
    provision = next(
        task for task in tasks if task.get("name") == "Ensure the agent app API key is stored in OpenBao"
    )

    assert provision["ansible.builtin.include_tasks"] == "ensure_agent_app_api_key.yml"
    assert provision["when"] == "dify_docker_dsl_name | length > 0"
    assert provision["no_log"] is True


def test_token_tasks_are_secret_safe_and_create_only_when_missing():
    tasks = _yaml(API_KEY_TASKS)
    by_name = {task["name"]: task for task in tasks}

    assert {READ_TASK, CREATE_TASK, STORE_TASK} <= by_name.keys()
    for name in (READ_TASK, CREATE_TASK, STORE_TASK):
        assert by_name[name]["no_log"] is True

    assert "community.hashi_vault.vault_kv2_get" in by_name[READ_TASK]
    assert by_name[CREATE_TASK]["ansible.builtin.uri"]["method"] == "POST"
    assert by_name[CREATE_TASK]["ansible.builtin.uri"]["status_code"] == 201
    assert any("api_key_lookup.secret.api_key" in condition for condition in by_name[CREATE_TASK]["when"])

    seed = by_name[STORE_TASK]["block"][0]
    assert seed["no_log"] is True
    assert seed["ansible.builtin.include_role"]["tasks_from"] == "seed_generated"
    fields = seed["vars"]["openbao_secrets_seed_fields"]
    assert fields[0]["name"] == "api_key"
    assert fields[0]["value"] == "{{ dify_docker_agent_app_api_key_create.json.token }}"
    assert seed["vars"]["openbao_secrets_seed_path"] == "{{ dify_docker_agent_app_api_key_path }}"


def test_api_key_leaf_is_derived_from_the_dify_openbao_base_path():
    defaults = _yaml(DEFAULTS)

    assert defaults["dify_docker_agent_app_api_key_path"] == (
        "{{ dify_docker_openbao_path }}/agent-apps/{{ dify_docker_dsl_name }}"
    )
