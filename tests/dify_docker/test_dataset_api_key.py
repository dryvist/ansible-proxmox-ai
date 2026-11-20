"""The workspace Knowledge API key is created once and stored in the Dify leaf."""

from jinja2 import Environment

from _agent_dsl_shared import ROOT, _yaml
from _defaults import load_defaults


TASKS = ROOT / "roles/dify_docker/tasks"
API_KEY_TASKS = TASKS / "ensure_dataset_api_key.yml"
READ_TASKS = TASKS / "read_agent_leaf.yml"
SEED_TASKS = TASKS / "seed_agent_leaf.yml"


def _tasks(items, inherited_no_log=False):
    for task in items:
        no_log = task.get("no_log", inherited_no_log)
        yield task, no_log
        for section in ("block", "rescue", "always"):
            yield from _tasks(task.get(section, []), no_log)


def _create_task():
    return next(task for task in _yaml(API_KEY_TASKS) if "ansible.builtin.uri" in task)


def _store_task():
    return next(
        task for task, _ in _tasks(_yaml(API_KEY_TASKS))
        if task.get("ansible.builtin.include_tasks") == "seed_agent_leaf.yml"
    )


def test_dataset_key_is_included_once_after_login_outside_the_agent_loop():
    includes = [
        (path, task)
        for path in sorted(TASKS.glob("*.yml"))
        for task, _ in _tasks(_yaml(path))
        if task.get("ansible.builtin.include_tasks") == "ensure_dataset_api_key.yml"
    ]
    assert len(includes) == 1
    path, provision = includes[0]
    assert path == TASKS / "setup_and_login.yml"
    setup = _yaml(path)
    session_index = next(
        index for index, task in enumerate(setup)
        if "dify_docker_console_cookies" in task.get("ansible.builtin.set_fact", {})
    )
    assert setup.index(provision) == session_index + 1
    assert provision["when"] == "dify_docker_provision_model_provider | bool"
    assert provision["args"]["apply"]["tags"] == ["dify_agent_import"]
    assert provision["tags"] == ["dify_agent_import"]
    assert "loop" not in provision
    assert provision["no_log"] is True

    main = _yaml(TASKS / "main.yml")
    setup_calls = [
        task for task in main
        if task.get("ansible.builtin.include_tasks") == "setup_and_login.yml"
    ]
    assert len(setup_calls) == 1
    assert "loop" not in setup_calls[0]
    agent_loop_index = next(
        index for index, task in enumerate(main)
        if task.get("ansible.builtin.include_tasks") == "import_agent_dsls.yml"
    )
    assert main.index(setup_calls[0]) < agent_loop_index


def test_dataset_key_reads_the_existing_dify_leaf_through_the_shared_helper():
    read = next(
        task for task in _yaml(API_KEY_TASKS)
        if task.get("ansible.builtin.include_tasks") == "read_agent_leaf.yml"
    )
    assert read["vars"] == {
        "dify_docker_agent_leaf_read_path": "{{ dify_docker_openbao_path }}",
        "dify_docker_agent_leaf_read_register": "dify_docker_dataset_api_key_lookup",
    }
    assert load_defaults()["dify_docker_openbao_path"] == "ai/dify"
    shared_read = next(task for task in _yaml(READ_TASKS) if "community.hashi_vault.vault_kv2_get" in task)
    assert shared_read["community.hashi_vault.vault_kv2_get"]["engine_mount_point"] == (
        "{{ openbao_secrets_kv_mount }}"
    )


def test_dataset_key_creation_is_gated_on_a_missing_or_empty_stored_field():
    create = _create_task()
    assert create["when"] == [
        "not ansible_check_mode",
        "((dify_docker_dataset_api_key_lookup.secret | default({})).DIFY_DATASET_API_KEY | default('', true)) | length == 0",
    ]
    condition = Environment().compile_expression(create["when"][1])
    for lookup in ({}, {"secret": {}}, {"secret": {"DIFY_DATASET_API_KEY": ""}}):
        assert condition(dify_docker_dataset_api_key_lookup=lookup) is True
    assert condition(dify_docker_dataset_api_key_lookup={"secret": {"DIFY_DATASET_API_KEY": "stored"}}) is False
    assert Environment().compile_expression(create["when"][0])(ansible_check_mode=True) is False


def test_dataset_key_create_posts_an_unscoped_workspace_key_with_the_console_session():
    create = _create_task()
    uri = create["ansible.builtin.uri"]
    assert uri["url"] == "{{ dify_docker_console_api_base }}/datasets/api-keys"
    assert uri["method"] == "POST"
    assert uri["body_format"] == "json"
    assert uri["body"] == {}
    assert uri["status_code"] == 200
    assert uri["return_content"] is True
    assert uri["headers"] == {
        "Cookie": "{{ dify_docker_console_cookies }}",
        "X-CSRF-Token": "{{ dify_docker_console_csrf_token }}",
    }
    assert create["register"] == "dify_docker_dataset_api_key_create"
    assert create["changed_when"] == "dify_docker_dataset_api_key_create.status | default(0) == 200"


def test_created_dataset_key_is_stored_by_the_shared_seed_helper():
    store_block = next(task for task in _yaml(API_KEY_TASKS) if "block" in task)
    assert store_block["when"] == "dify_docker_dataset_api_key_create.status | default(0) == 200"
    assert len(store_block["block"]) == 1
    seed = _store_task()["vars"]
    assert seed["dify_docker_agent_leaf_seed_path"] == "{{ dify_docker_openbao_path }}"
    assert seed["dify_docker_agent_leaf_seed_register"] == "dify_docker_dataset_api_key_seed_result"
    assert seed["dify_docker_agent_leaf_seed_fields"] == [{
        "name": "DIFY_DATASET_API_KEY",
        "length": 50,
        "charset": "alnum",
        "value": "{{ dify_docker_dataset_api_key_create.json.token }}",
    }]
    assert "dify_docker_agent_leaf_seed_rotate_fields" not in seed


def test_failed_dataset_key_storage_deletes_the_created_id_then_fails():
    rollback_url = " ".join(_store_task()["vars"]["dify_docker_agent_leaf_seed_rollback_url"].split())
    assert rollback_url == (
        "{{ dify_docker_console_api_base }}/datasets/api-keys/{{ dify_docker_dataset_api_key_create.json.id }}"
    )
    seed_block = next(task for task in _yaml(SEED_TASKS) if "block" in task)
    readback, delete, fail = seed_block["rescue"]
    assert readback["ansible.builtin.include_tasks"] == "read_agent_leaf.yml"
    assert delete["ansible.builtin.uri"]["url"] == "{{ dify_docker_agent_leaf_seed_rollback_url }}"
    assert delete["ansible.builtin.uri"]["method"] == "DELETE"
    assert delete["ansible.builtin.uri"]["status_code"] == 204
    assert delete["ansible.builtin.uri"]["headers"] == _create_task()["ansible.builtin.uri"]["headers"]
    assert "dify_docker_agent_leaf_seed_rollback_url is defined" in delete["when"]
    assert "dify_docker_agent_leaf_seed_fields[0].name" in delete["when"]
    assert "dify_docker_agent_leaf_seed_fields[0].value" in delete["when"]
    assert fail["ansible.builtin.fail"]["msg"] == "The Dify leaf could not be stored in OpenBao."


def test_all_dataset_key_and_shared_helper_actions_suppress_secret_logging():
    for path in (API_KEY_TASKS, READ_TASKS, SEED_TASKS):
        for task, no_log in _tasks(_yaml(path)):
            if any(key.startswith(("ansible.builtin.", "community.hashi_vault.")) for key in task):
                assert no_log is True, (path.name, task["name"])
