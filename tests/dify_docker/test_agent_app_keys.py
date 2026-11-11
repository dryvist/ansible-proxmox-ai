"""Agent app API keys are created once and stored in per-app OpenBao leaves."""

from pathlib import Path

from _agent_dsl_shared import _yaml
from _defaults import load_defaults


ROOT = Path(__file__).resolve().parents[2]
IMPORT_TASKS = ROOT / "roles/dify_docker/tasks/import_agent_dsl.yml"
API_KEY_TASKS = ROOT / "roles/dify_docker/tasks/ensure_agent_app_api_key.yml"
READ_TASKS = ROOT / "roles/dify_docker/tasks/read_agent_leaf.yml"
SEED_TASKS = ROOT / "roles/dify_docker/tasks/seed_agent_leaf.yml"

READ_TASK = "Read the stored Dify agent app API key from OpenBao"
CREATE_TASK = "Create the Dify agent app API key only when OpenBao has none"
STORE_TASK = "Store the Dify agent app API key through the shared seed_generated task"


def test_api_key_provisioning_is_wired_after_each_agent_dsl():
    tasks = _yaml(IMPORT_TASKS)
    provision = next(
        task for task in tasks if task.get("name") == "Ensure the agent app API key is stored in OpenBao"
    )

    assert provision["ansible.builtin.include_tasks"] == "ensure_agent_app_api_key.yml"
    assert provision["when"][:3] == [
        "dify_docker_dsl_name | length > 0",
        "dify_docker_dsl_content | length > 0",
        "not ansible_check_mode",
    ]
    assert "not (dify_docker_dsl_import_needed | bool)" in provision["when"][3]
    assert "completed" in provision["when"][3]
    assert "completed-with-warnings" in provision["when"][3]
    assert not any("dify_docker_dsl_app_matches | length == 0" in condition for condition in provision["when"])
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

    # The leaf read lives in the shared file; the caller binds the API-key
    # leaf path and the register name the create condition reads.
    read_include = top_level_by_name[READ_TASK]
    assert read_include["ansible.builtin.include_tasks"] == "read_agent_leaf.yml"
    assert read_include["vars"] == {
        "dify_docker_agent_leaf_read_path": "{{ dify_docker_agent_app_api_key_path }}",
        "dify_docker_agent_leaf_read_register": "dify_docker_agent_app_api_key_lookup",
    }
    shared_read = next(task for task in _yaml(READ_TASKS) if "community.hashi_vault.vault_kv2_get" in task)
    assert "community.hashi_vault.vault_kv2_get" in shared_read
    assert shared_read["no_log"] is True

    assert top_level_by_name[CREATE_TASK]["ansible.builtin.uri"]["method"] == "POST"
    assert top_level_by_name[CREATE_TASK]["ansible.builtin.uri"]["status_code"] == 201
    assert top_level_by_name[CREATE_TASK]["when"] == [
        "not ansible_check_mode",
        "dify_docker_agent_app_id | length > 0",
        "((dify_docker_agent_app_api_key_lookup.secret | default({})).api_key | default('', true)) | length == 0",
    ]
    assert api_key_block["when"] == "dify_docker_agent_app_api_key_create.status | default(0) == 201"

    seed = by_name[STORE_TASK]
    fields = seed["vars"]["dify_docker_agent_leaf_seed_fields"]
    assert fields[0]["name"] == "api_key"
    assert fields[0]["value"] == "{{ dify_docker_agent_app_api_key_create.json.token }}"
    assert seed["vars"]["dify_docker_agent_leaf_seed_path"] == "{{ dify_docker_agent_app_api_key_path }}"


def test_seed_rescue_reads_leaf_before_deleting_created_key():
    tasks = _yaml(API_KEY_TASKS)
    api_key_block = next(task for task in tasks if "block" in task)
    top_level_by_name = {task["name"]: task for task in tasks}

    assert CREATE_TASK in top_level_by_name
    assert len(api_key_block["block"]) == 1

    seed_block = next(task for task in _yaml(SEED_TASKS) if "block" in task)
    rescue = seed_block["rescue"]

    assert len(rescue) == 3
    readback, delete, fail = rescue
    # The read-back reuses the shared leaf read under the register the rollback reads.
    assert readback["ansible.builtin.include_tasks"] == "read_agent_leaf.yml"
    assert readback["vars"] == {
        "dify_docker_agent_leaf_read_path": "{{ dify_docker_agent_leaf_seed_path }}",
        "dify_docker_agent_leaf_read_register": "dify_docker_agent_leaf_seed_readback",
    }
    assert readback["no_log"] is True
    assert delete["ansible.builtin.uri"]["method"] == "DELETE"
    assert delete["ansible.builtin.uri"]["status_code"] == 204
    assert delete["ansible.builtin.uri"]["headers"] == top_level_by_name[CREATE_TASK][
        "ansible.builtin.uri"
    ]["headers"]
    assert delete["ansible.builtin.uri"]["url"] == "{{ dify_docker_agent_leaf_seed_rollback_url }}"
    # The rollback only runs when a caller bound one and the leaf does not
    # hold the value that caller created.
    assert "dify_docker_agent_leaf_seed_rollback_url is defined" in delete["when"]
    assert "dify_docker_agent_leaf_seed_fields[0].name" in delete["when"]
    assert "dify_docker_agent_leaf_seed_fields[0].value" in delete["when"]
    assert delete["no_log"] is True
    assert fail["ansible.builtin.fail"]["msg"] == (
        "The agent app leaf could not be stored in OpenBao."
    )
    assert fail["no_log"] is True

    # The API-key caller binds the rollback to the key Dify just created.
    store = next(task for task in api_key_block["block"] if task["name"] == STORE_TASK)
    rollback_url = store["vars"]["dify_docker_agent_leaf_seed_rollback_url"]
    assert rollback_url.startswith(
        "{{ dify_docker_console_api_base }}/apps/{{ dify_docker_agent_app_id }}/api-keys/"
    )
    assert "dify_docker_agent_app_api_key_create.json.id" in rollback_url


def test_api_key_leaf_is_derived_from_the_dify_openbao_base_path():
    defaults = load_defaults()

    assert defaults["dify_docker_agent_app_api_key_path"] == (
        "{{ dify_docker_openbao_path }}/{{ dify_docker_agent_app_leaf_segment }}/{{ dify_docker_dsl_name }}"
    )


def test_the_agent_apps_path_segment_is_defined_once_and_used_through_its_variable():
    hits = [line.strip() for path in sorted((ROOT / "roles/dify_docker").rglob("*.yml"))
            for line in path.read_text(encoding="utf-8").splitlines() if "agent-apps" in line]
    assert hits == ["dify_docker_agent_app_leaf_segment: agent-apps"]
    assert load_defaults()["dify_docker_agent_app_leaf_segment"] == "agent-apps"


def test_openbao_reads_take_validate_certs_from_a_role_default():
    # Every OpenBao leaf read, the seed read-back included, goes through read_agent_leaf.yml.
    reads = [
        task["community.hashi_vault.vault_kv2_get"]
        for task in _yaml(READ_TASKS)
        if "community.hashi_vault.vault_kv2_get" in task
    ]

    assert len(reads) == 1
    assert {read["validate_certs"] for read in reads} == {"{{ dify_docker_bao_validate_certs }}"}
    assert load_defaults()["dify_docker_bao_validate_certs"] is True
