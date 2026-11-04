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
APP_ASSERT_TASK = "Assert the Dify agent app was found"


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
    api_key_block = next(task for task in tasks if "block" in task)
    by_name = {task["name"]: task for task in api_key_block["block"]}

    top_level_by_name = {task["name"]: task for task in tasks}
    assert STORE_TASK in by_name
    assert CREATE_TASK in top_level_by_name
    assert READ_TASK in top_level_by_name
    assert top_level_by_name[CREATE_TASK]["no_log"] is True
    assert by_name[STORE_TASK]["no_log"] is True
    assert top_level_by_name[READ_TASK]["no_log"] is True

    assert "community.hashi_vault.vault_kv2_get" in top_level_by_name[READ_TASK]
    assert top_level_by_name[CREATE_TASK]["ansible.builtin.uri"]["method"] == "POST"
    assert top_level_by_name[CREATE_TASK]["ansible.builtin.uri"]["status_code"] == 201
    assert top_level_by_name[CREATE_TASK]["when"] == [
        "not ansible_check_mode",
        "dify_docker_agent_app_id | length > 0",
        "((dify_docker_agent_app_api_key_lookup.secret | default({})).api_key | default('', true)) | length == 0",
    ]
    assert api_key_block["when"] == "dify_docker_agent_app_api_key_create.status | default(0) == 201"

    seed = by_name[STORE_TASK]
    assert seed["ansible.builtin.include_role"]["tasks_from"] == "seed_generated"
    fields = seed["vars"]["openbao_secrets_seed_fields"]
    assert fields[0]["name"] == "api_key"
    assert fields[0]["value"] == "{{ dify_docker_agent_app_api_key_create.json.token }}"
    assert seed["vars"]["openbao_secrets_seed_path"] == "{{ dify_docker_agent_app_api_key_path }}"


def test_seed_rescue_reads_leaf_before_deleting_created_key():
    tasks = _yaml(API_KEY_TASKS)
    api_key_block = next(task for task in tasks if "block" in task)
    top_level_by_name = {task["name"]: task for task in tasks}
    block_names = {task["name"] for task in api_key_block["block"]}
    rescue = api_key_block["rescue"]

    assert CREATE_TASK in top_level_by_name
    assert block_names == {STORE_TASK}
    assert len(rescue) == 3
    readback, delete, fail = rescue
    assert "community.hashi_vault.vault_kv2_get" in readback
    assert readback["register"] == "dify_docker_agent_app_api_key_readback"
    assert readback["community.hashi_vault.vault_kv2_get"]["path"] == "{{ dify_docker_agent_app_api_key_path }}"
    assert readback["no_log"] is True
    assert delete["ansible.builtin.uri"]["method"] == "DELETE"
    assert delete["ansible.builtin.uri"]["status_code"] == 204
    assert delete["ansible.builtin.uri"]["headers"] == top_level_by_name[CREATE_TASK][
        "ansible.builtin.uri"
    ]["headers"]
    assert delete["when"] == (
        "((dify_docker_agent_app_api_key_readback.secret | default({})).api_key "
        "| default('', true)) != dify_docker_agent_app_api_key_create.json.token"
    )
    assert delete["no_log"] is True
    delete_url = delete["ansible.builtin.uri"]["url"]
    assert delete_url.startswith(
        "{{ dify_docker_console_api_base }}/apps/{{ dify_docker_agent_app_id }}/api-keys/"
    )
    assert "dify_docker_agent_app_api_key_create.json.id" in delete_url
    assert fail["ansible.builtin.fail"]["msg"] == (
        "The Dify agent app API key could not be stored in OpenBao."
    )
    assert fail["no_log"] is True


def test_app_list_is_filtered_by_dsl_name_and_resolution_stays_exact():
    tasks = _yaml(API_KEY_TASKS)
    app_list = next(
        task
        for task in tasks
        if task["name"] == "List Dify apps to resolve the agent app id"
    )
    resolve = next(
        task for task in tasks if task["name"] == "Resolve the Dify agent app id by name"
    )

    assert app_list["ansible.builtin.uri"]["url"] == (
        "{{ dify_docker_console_api_base }}/apps?page=1&limit=100&name="
        "{{ dify_docker_dsl_name | urlencode }}"
    )
    assert "selectattr('name', 'equalto', dify_docker_dsl_name)" in (
        resolve["ansible.builtin.set_fact"]["dify_docker_agent_app_id"]
    )


def test_missing_agent_app_fails_with_assert():
    tasks = _yaml(API_KEY_TASKS)
    resolve_index = next(
        index for index, task in enumerate(tasks) if task["name"] == "Resolve the Dify agent app id by name"
    )
    assertion = tasks[resolve_index + 1]

    assert assertion["name"] == APP_ASSERT_TASK
    assert assertion["ansible.builtin.assert"]["that"] == [
        "dify_docker_agent_app_id | length > 0"
    ]
    assert "{{ dify_docker_dsl_name }}" in assertion["ansible.builtin.assert"]["fail_msg"]
    assert assertion.get("no_log") is not True


def test_api_key_leaf_is_derived_from_the_dify_openbao_base_path():
    defaults = _yaml(DEFAULTS)

    assert defaults["dify_docker_agent_app_api_key_path"] == (
        "{{ dify_docker_openbao_path }}/agent-apps/{{ dify_docker_dsl_name }}"
    )
