"""Agent app ids resolve by exact name over a bounded page loop and fail on a duplicate or a miss."""

import yaml
from jinja2 import Environment

from _agent_dsl_shared import ROOT, _yaml
from _defaults import load_defaults


APP_PAGE_TASKS = ROOT / "roles/dify_docker/tasks/list_agent_app_page.yml"
RESOLVE_TASKS = ROOT / "roles/dify_docker/tasks/resolve_agent_app_id.yml"

APP_ASSERT_TASK = "Assert the Dify agent app was found"
DUPLICATE_APP_ASSERT_MSG = (
    'More than one Dify app has the agent name "{{ dify_docker_dsl_name }}"; '
    "resolve the duplicate before continuing."
)


def _render_yaml_expression(expression, **context):
    rendered = Environment().from_string(expression).render(context)
    return yaml.safe_load(rendered) if rendered else ""


def test_app_list_is_filtered_by_dsl_name_and_resolution_stays_exact():
    tasks = _yaml(RESOLVE_TASKS)
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
    assert include_pages["loop"] == "{{ range(1, (dify_docker_app_lookup_max_pages | int) + 1) | list }}"
    assert include_pages["loop_control"] == {"loop_var": "dify_docker_app_page"}
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


def test_app_list_pagination_is_a_bounded_loop_with_cap_and_shape_guards():
    # Recursive include_tasks cannot reference its own page variable: Ansible
    # raises "Recursive loop detected", so pages come from a bounded loop.
    tasks = _yaml(RESOLVE_TASKS)
    page_tasks = _yaml(APP_PAGE_TASKS)
    page_loop = next(
        task for task in tasks if task.get("ansible.builtin.include_tasks") == "list_agent_app_page.yml"
    )
    more_record = next(
        task for task in page_tasks if task["name"] == "Record whether another page follows"
    )
    cap_fail = next(
        task
        for task in page_tasks
        if task.get("ansible.builtin.fail", {}).get("msg")
        == "Too many Dify apps match the agent name to check for duplicates; narrow the name."
    )
    missing_has_more_fail = next(
        task
        for task in page_tasks
        if task.get("ansible.builtin.fail", {}).get("msg")
        == "Dify app list response has no boolean has_more field."
    )

    assert page_loop["loop"] == "{{ range(1, (dify_docker_app_lookup_max_pages | int) + 1) | list }}"
    assert page_loop["loop_control"] == {"loop_var": "dify_docker_app_page"}
    assert more_record["ansible.builtin.set_fact"]["dify_docker_agent_apps_more"] == (
        "{{ dify_docker_agent_apps_page.json.has_more }}"
    )
    assert not any("ansible.builtin.include_tasks" in task for task in page_tasks)
    # Every page task must check the stop flag when it runs, not when the loop expands.
    for task in page_tasks:
        when = task.get("when")
        conditions = when if isinstance(when, list) else [when]
        assert "dify_docker_agent_apps_more | bool" in conditions, task["name"]
    assert cap_fail["when"] == [
        "dify_docker_agent_apps_more | bool",
        "dify_docker_agent_apps_page.json.has_more is true",
        "dify_docker_app_page | int >= dify_docker_app_lookup_max_pages | int",
    ]
    assert missing_has_more_fail["when"] == [
        "dify_docker_agent_apps_more | bool",
        "dify_docker_agent_apps_page.json.has_more is not boolean",
    ]


def test_duplicate_agent_apps_fail_before_agent_app_id_is_used():
    tasks = _yaml(RESOLVE_TASKS)
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
    tasks = _yaml(RESOLVE_TASKS)
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
    tasks = _yaml(RESOLVE_TASKS)
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


def test_app_lookup_expands_a_small_page_count():
    # Each expanded page costs five tasks even when skipped, so a large cap slows every app import.
    assert load_defaults()["dify_docker_app_lookup_max_pages"] == 2


def test_every_app_lookup_caller_uses_the_page_cap():
    loop = "{{ range(1, (dify_docker_app_lookup_max_pages | int) + 1) | list }}"
    callers = [
        task
        for path in sorted((ROOT / "roles/dify_docker/tasks").glob("*.yml"))
        for task in _yaml(path) or []
        if isinstance(task, dict) and task.get("ansible.builtin.include_tasks") == "list_agent_app_page.yml"
    ]

    assert len(callers) == 3
    assert all(task["loop"] == loop for task in callers)
