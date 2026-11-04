"""Agent app API keys are created once and stored in per-app OpenBao leaves."""

from pathlib import Path

import yaml
from jinja2 import Environment


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
IMPORT_TASKS = ROOT / "roles/dify_docker/tasks/import_agent_dsl.yml"
API_KEY_TASKS = ROOT / "roles/dify_docker/tasks/ensure_agent_app_api_key.yml"
APP_PAGE_TASKS = ROOT / "roles/dify_docker/tasks/list_agent_app_page.yml"

READ_TASK = "Read the stored Dify agent app API key from OpenBao"
CREATE_TASK = "Create the Dify agent app API key only when OpenBao has none"
STORE_TASK = "Store the Dify agent app API key through the shared seed_generated task"
APP_ASSERT_TASK = "Assert the Dify agent app was found"
DUPLICATE_APP_ASSERT_MSG = (
    "More than one Dify app has the agent name; resolve the duplicate before "
    "provisioning the agent key."
)


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _render_yaml_expression(expression, **context):
    rendered = Environment().from_string(expression).render(context)
    return yaml.safe_load(rendered) if rendered else ""


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
    page_tasks = _yaml(APP_PAGE_TASKS)
    initialize = next(
        task for task in tasks if "dify_docker_agent_apps_all" in task.get("ansible.builtin.set_fact", {})
    )
    include_pages = next(
        task for task in tasks if task.get("ansible.builtin.include_tasks") == "list_agent_app_page.yml"
    )
    app_list = next(
        task for task in page_tasks
        if task["name"] == "List one page of Dify apps to resolve the agent app id"
    )
    matches = next(
        task for task in tasks if task["name"] == "Resolve exact Dify agent app matches by name"
    )
    resolve = next(
        task for task in tasks if task["name"] == "Resolve the Dify agent app id by name"
    )

    assert initialize["ansible.builtin.set_fact"]["dify_docker_agent_apps_all"] == []
    assert initialize["no_log"] is True
    assert include_pages["vars"]["dify_docker_app_page"] == 1
    assert include_pages["no_log"] is True
    assert app_list["ansible.builtin.uri"]["url"] == (
        "{{ dify_docker_console_api_base }}/apps?page={{ dify_docker_app_page }}&limit=100&name="
        "{{ dify_docker_dsl_name | urlencode }}"
    )
    assert app_list["no_log"] is True
    assert "selectattr('name', 'equalto', dify_docker_dsl_name)" in (
        matches["ansible.builtin.set_fact"]["dify_docker_agent_app_matches"]
    )
    assert "dify_docker_agent_apps_all" in (
        matches["ansible.builtin.set_fact"]["dify_docker_agent_app_matches"]
    )
    assert resolve["ansible.builtin.set_fact"]["dify_docker_agent_app_id"] == (
        "{{ dify_docker_agent_app_matches[0].id | default('') }}"
    )


def test_recursive_app_list_pagination_has_cap_and_shape_guards():
    assert APP_PAGE_TASKS.exists()
    tasks = _yaml(APP_PAGE_TASKS)
    recursive_include = next(
        task
        for task in tasks
        if task.get("ansible.builtin.include_tasks") == "list_agent_app_page.yml"
    )
    cap_fail = next(
        task
        for task in tasks
        if task.get("ansible.builtin.fail", {}).get("msg")
        == "Too many Dify apps match the agent name to check for duplicates; narrow the name."
    )
    missing_has_more_fail = next(
        task
        for task in tasks
        if task.get("ansible.builtin.fail", {}).get("msg")
        == "Dify app list response has no has_more field."
    )

    assert recursive_include["vars"]["dify_docker_app_page"] == (
        "{{ dify_docker_app_page | int + 1 }}"
    )
    assert recursive_include["when"] == [
        "dify_docker_agent_apps_page.json.has_more is true",
        "dify_docker_app_page | int < 50",
    ]
    assert cap_fail["when"] == [
        "dify_docker_agent_apps_page.json.has_more is true",
        "dify_docker_app_page | int >= 50",
    ]
    assert missing_has_more_fail["when"] == (
        "dify_docker_agent_apps_page.json.has_more is not defined"
    )


def test_duplicate_agent_apps_fail_before_agent_app_id_is_used():
    tasks = _yaml(API_KEY_TASKS)
    uniqueness_assertions = [
        (index, task)
        for index, task in enumerate(tasks)
        if task.get("ansible.builtin.assert", {}).get("fail_msg") == DUPLICATE_APP_ASSERT_MSG
    ]

    assert len(uniqueness_assertions) == 1
    uniqueness_index, uniqueness_assertion = uniqueness_assertions[0]
    assert uniqueness_assertion["ansible.builtin.assert"]["that"] == [
        "dify_docker_agent_app_matches | length <= 1"
    ]
    assert uniqueness_assertion.get("no_log") is False

    app_id_task_indices = [
        index
        for index, task in enumerate(tasks)
        if "dify_docker_agent_app_id" in yaml.safe_dump(task)
    ]
    assert app_id_task_indices
    assert uniqueness_index < min(app_id_task_indices)


def test_missing_agent_app_fails_with_assert():
    tasks = _yaml(API_KEY_TASKS)
    resolve_index = next(
        index for index, task in enumerate(tasks) if task["name"] == "Resolve the Dify agent app id by name"
    )
    assertion_index = next(
        index for index, task in enumerate(tasks) if task["name"] == APP_ASSERT_TASK
    )
    assertion = tasks[assertion_index]

    assert assertion_index > resolve_index
    assert assertion["name"] == APP_ASSERT_TASK
    assert assertion["ansible.builtin.assert"]["that"] == [
        "dify_docker_agent_app_id | length > 0"
    ]
    assert "{{ dify_docker_dsl_name }}" in assertion["ansible.builtin.assert"]["fail_msg"]
    assert assertion.get("no_log") is not True


def test_exact_name_uniqueness_uses_apps_aggregated_from_all_pages():
    tasks = _yaml(API_KEY_TASKS)
    page_tasks = _yaml(APP_PAGE_TASKS)
    accumulate = next(
        task for task in page_tasks if task["name"] == "Accumulate this page of Dify apps"
    )
    matches_task = next(
        task for task in tasks if task["name"] == "Resolve exact Dify agent app matches by name"
    )
    matches_assertion = next(
        task
        for task in tasks
        if task.get("ansible.builtin.assert", {}).get("fail_msg") == DUPLICATE_APP_ASSERT_MSG
    )
    id_task = next(
        task for task in tasks if task["name"] == "Resolve the Dify agent app id by name"
    )
    found_assertion = next(task for task in tasks if task["name"] == APP_ASSERT_TASK)

    accumulate_expression = accumulate["ansible.builtin.set_fact"]["dify_docker_agent_apps_all"]
    matches_expression = matches_task["ansible.builtin.set_fact"]["dify_docker_agent_app_matches"]
    uniqueness_expression = matches_assertion["ansible.builtin.assert"]["that"][0]
    app_id_expression = id_task["ansible.builtin.set_fact"]["dify_docker_agent_app_id"]
    found_expression = found_assertion["ansible.builtin.assert"]["that"][0]

    def check_pages(page_one_data, page_two_data):
        pages = [
            {"json": {"data": page_one_data, "has_more": True}},
            {"json": {"data": page_two_data, "has_more": False}},
        ]
        all_apps = []
        for page in pages:
            all_apps = _render_yaml_expression(
                accumulate_expression,
                dify_docker_agent_apps_all=all_apps,
                dify_docker_agent_apps_page=page,
            )
        matches = _render_yaml_expression(
            matches_expression,
            dify_docker_agent_apps_all=all_apps,
            dify_docker_dsl_name="agent-app",
        )
        uniqueness_passes = Environment().compile_expression(uniqueness_expression)(
            dify_docker_agent_app_matches=matches
        )
        app_id = _render_yaml_expression(
            app_id_expression, dify_docker_agent_app_matches=matches
        )
        found_passes = Environment().compile_expression(found_expression)(
            dify_docker_agent_app_id=app_id
        )
        return pages, all_apps, matches, uniqueness_passes, found_passes

    first_page = [{"id": "first", "name": "agent-app"}]
    duplicate_page = [{"id": "second", "name": "agent-app"}]
    pages, all_apps, matches, uniqueness_passes, _ = check_pages(first_page, duplicate_page)
    assert [page["json"]["has_more"] for page in pages] == [True, False]
    assert [app["id"] for app in all_apps] == ["first", "second"]
    assert [app["id"] for app in matches] == ["first", "second"]
    assert uniqueness_passes is False

    _, _, matches, uniqueness_passes, found_passes = check_pages(
        first_page, [{"id": "substring", "name": "agent-app-copy"}]
    )
    assert [app["id"] for app in matches] == ["first"]
    assert uniqueness_passes is True
    assert found_passes is True


def test_api_key_leaf_is_derived_from_the_dify_openbao_base_path():
    defaults = _yaml(DEFAULTS)

    assert defaults["dify_docker_agent_app_api_key_path"] == (
        "{{ dify_docker_openbao_path }}/agent-apps/{{ dify_docker_dsl_name }}"
    )
