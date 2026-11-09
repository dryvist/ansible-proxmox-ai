"""Dify agent app listing, reconciliation, and workflow publication."""

import ast

from jinja2 import Environment

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE, LIST_CATALOG_PAGE, ROOT, _expanded_yaml, _task, _yaml


def test_catalog_app_listing_fetches_and_validates_every_page():
    tasks = _yaml(IMPORT_LOOP)
    page_tasks = _yaml(LIST_CATALOG_PAGE)
    by_name = {task.get("name"): task for task in tasks}
    first_page = by_name["List first page of existing Dify apps (agent DSL catalog hash check)"]
    initialize = by_name["Initialize the catalog Dify app list and page count"]
    later_pages = by_name["List every remaining Dify app page"]
    page_loop = _task(page_tasks, "List one page of Dify apps for the agent catalog")
    accumulate = _task(page_tasks, "Accumulate a Dify app catalog page")
    complete_check = by_name["Verify every Dify app was listed exactly once"]

    assert first_page["ansible.builtin.uri"]["url"].endswith("/apps?page=1&limit=100")
    assert "failed_when" not in first_page
    assert initialize["ansible.builtin.set_fact"]["dify_docker_dsl_apps_all"] == (
        "{{ dify_docker_dsl_apps_list.json.data }}"
    )
    assert "json.total" in initialize["ansible.builtin.set_fact"]["dify_docker_dsl_apps_page_count"]
    assert later_pages["ansible.builtin.include_tasks"] == "list_catalog_app_page.yml"
    assert later_pages["loop"] == "{{ range(2, (dify_docker_dsl_apps_page_count | int) + 1) | list }}"
    assert later_pages["loop_control"]["loop_var"] == "dify_docker_dsl_page"
    assert "page={{ dify_docker_dsl_page }}" in page_loop["ansible.builtin.uri"]["url"]
    assert "limit={{ dify_docker_dsl_apps_limit }}" in page_loop["ansible.builtin.uri"]["url"]
    assert "dify_docker_dsl_apps_all + dify_docker_dsl_apps_page.json.data" in (
        accumulate["ansible.builtin.set_fact"]["dify_docker_dsl_apps_all"]
    )
    assert "dify_docker_dsl_apps_total | int" in str(complete_check["ansible.builtin.assert"]["that"])
    assert "unique" in str(complete_check["ansible.builtin.assert"]["that"])

    page_count = initialize["ansible.builtin.set_fact"]["dify_docker_dsl_apps_page_count"]
    for total, expected_pages in ((0, 0), (100, 1), (101, 2), (201, 3)):
        rendered_count = Environment().from_string(page_count).render(
            dify_docker_dsl_apps_list={"json": {"total": total, "limit": 100}}
        )
        assert int(rendered_count) == expected_pages
    rendered_loop = Environment().from_string(later_pages["loop"]).render(
        dify_docker_dsl_apps_page_count=3
    )
    assert ast.literal_eval(rendered_loop) == [2, 3]


def test_changed_workflow_is_published_after_successful_import_and_failure_is_fatal():
    importer = _expanded_yaml(IMPORT_ONE)
    environment = Environment()
    tasks_by_name = {task.get("name"): (index, task) for index, task in enumerate(importer)}
    _, import_task = tasks_by_name["Import the catalog DSL when reconciliation requires it"]
    _, confirm_task = tasks_by_name["Confirm the agent DSL import (DSL version mismatch)"]
    app_id_index, app_id_task = tasks_by_name["Resolve the Dify app id returned by the catalog import"]
    publish_index, publish_task = tasks_by_name["Publish the changed catalog workflow for API callers"]
    metadata_index, metadata_task = tasks_by_name[
        "Load the app record after publishing"
    ]
    record_index, record_task = tasks_by_name["Record the catalog hash after publishing the workflow"]
    key_index, key_task = tasks_by_name["Ensure the agent app API key is stored in OpenBao"]

    assert import_task["failed_when"] != "false"
    assert "completed" in import_task["failed_when"]
    assert "completed-with-warnings" in import_task["failed_when"]
    assert "pending" in import_task["failed_when"]
    assert confirm_task["failed_when"] != "false"
    assert "completed" in confirm_task["failed_when"]
    assert "completed-with-warnings" in confirm_task["failed_when"]
    assert "dify_docker_dsl_confirm.json" in app_id_task["ansible.builtin.set_fact"][
        "dify_docker_dsl_imported_app_id"
    ]
    assert publish_index > app_id_index
    assert publish_index < key_index
    assert publish_task["ansible.builtin.uri"]["url"] == (
        "{{ dify_docker_console_api_base }}/apps/{{ dify_docker_dsl_imported_app_id }}"
        "/workflows/publish"
    )
    assert publish_task["ansible.builtin.uri"]["method"] == "POST"
    assert publish_task["ansible.builtin.uri"]["status_code"] == 200
    assert "catalog sha256:" in publish_task["ansible.builtin.uri"]["body"]["marked_comment"]
    assert "dify_docker_dsl_import_needed | bool" in publish_task["when"]
    assert "not ansible_check_mode" in publish_task["when"]
    assert publish_task["failed_when"] != "false"
    assert "success" in publish_task["failed_when"]
    assert publish_task["no_log"] is True
    assert metadata_index > publish_index
    assert metadata_index < record_index
    assert metadata_task["ansible.builtin.uri"]["method"] == "GET"
    assert metadata_task["no_log"] is True
    assert record_index > publish_index
    assert record_index < key_index
    assert record_task["ansible.builtin.uri"]["url"] == (
        "{{ dify_docker_console_api_base }}/apps/{{ dify_docker_dsl_imported_app_id }}"
    )
    assert record_task["ansible.builtin.uri"]["method"] == "PUT"
    assert record_task["ansible.builtin.uri"]["status_code"] == 200
    assert "catalog sha256:" in record_task["ansible.builtin.uri"]["body"]
    assert "dify_docker_dsl_publish.json" in record_task["when"][-1]
    assert "success" in record_task["when"][-1]
    assert record_task["failed_when"] != "false"
    assert "dify_docker_dsl_imported_app_id" in record_task["failed_when"]
    assert record_task["no_log"] is True
    assert "mode" not in record_task["ansible.builtin.uri"]["body"]
    assert "api_key" not in record_task["ansible.builtin.uri"]["body"]
    record_payload = ast.literal_eval(
        environment.from_string(record_task["ansible.builtin.uri"]["body"]).render(
            dify_docker_dsl_name="catalog-app",
            dify_docker_dsl_catalog_description="Catalog description",
            dify_docker_agent_dsl={"sha256": "a" * 64},
            dify_docker_dsl_app_metadata={
                "json": {
                    "icon_type": "emoji",
                    "icon": "sparkles",
                    "icon_background": "#FFFFFF",
                    "use_icon_as_answer_icon": True,
                    "max_active_requests": 10,
                }
            },
        )
    )
    assert record_payload == {
        "name": "catalog-app",
        "description": "Catalog description [catalog sha256:" + "a" * 64 + "]",
        "icon_type": "emoji",
        "icon": "sparkles",
        "icon_background": "#FFFFFF",
        "use_icon_as_answer_icon": True,
        "max_active_requests": 10,
    }
    assert "not (dify_docker_dsl_import_needed | bool)" in key_task["when"][-1]

    import_failure = environment.from_string("{{ " + import_task["failed_when"] + " }}")
    confirm_failure = environment.from_string("{{ " + confirm_task["failed_when"] + " }}")
    publish_failure = environment.from_string("{{ " + publish_task["failed_when"] + " }}")
    record_after_publish_success = environment.from_string("{{ " + record_task["when"][-1] + " }}")
    for completed_status in ("completed", "completed-with-warnings"):
        assert import_failure.render(dify_docker_dsl_import={"json": {"status": completed_status}}) == "False"
        assert confirm_failure.render(dify_docker_dsl_confirm={"json": {"status": completed_status}}) == "False"
    assert import_failure.render(dify_docker_dsl_import={"json": {"status": "pending"}}) == "False"
    assert import_failure.render(dify_docker_dsl_import={"json": {"status": "failed"}}) == "True"
    assert confirm_failure.render(dify_docker_dsl_confirm={"json": {"status": "failed"}}) == "True"
    assert publish_failure.render(dify_docker_dsl_publish={"json": {"result": "success"}}) == "False"
    assert publish_failure.render(dify_docker_dsl_publish={"json": {"result": "error"}}) == "True"
    assert record_after_publish_success.render(dify_docker_dsl_publish={"json": {"result": "success"}}) == "True"
    assert record_after_publish_success.render(dify_docker_dsl_publish={"json": {"result": "error"}}) == "False"


def test_catalog_hash_decides_whether_to_import_and_payload_targets_existing_app():
    importer = _expanded_yaml(IMPORT_ONE)
    decision_task = _task(importer, "Decide whether the catalog app needs an import")
    payload_task = _task(importer, "Prepare the Dify import payload")
    import_task = _task(importer, "Import the catalog DSL when reconciliation requires it")
    defaults = _yaml(ROOT / "roles/dify_docker/defaults/main/20-agent-apps.yml")
    environment = Environment()
    environment.filters["bool"] = lambda value: value is True or str(value).lower() in (
        "true", "yes", "on", "1"
    )
    environment.filters["combine"] = lambda base, extra: {**base, **extra}
    digest = "a" * 64
    old_digest = "b" * 64
    assert defaults["dify_docker_agent_dsl_update_existing"] is False

    decision = decision_task["ansible.builtin.set_fact"]["dify_docker_dsl_import_needed"]
    unchanged = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[
            {"id": "app-1", "description": f"notes [catalog sha256:{digest}]"}
        ],
        dify_docker_agent_dsl={"sha256": digest, "private": False},
        dify_docker_agent_dsl_update_existing=False,
        dify_docker_dsl_existing_app={
            "id": "app-1", "description": f"notes [catalog sha256:{digest}]"
        },
    )
    changed = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[
            {"id": "app-1", "description": f"notes [catalog sha256:{old_digest}]"}
        ],
        dify_docker_agent_dsl={"sha256": digest, "private": False},
        dify_docker_agent_dsl_update_existing=False,
        dify_docker_dsl_existing_app={
            "id": "app-1", "description": f"notes [catalog sha256:{old_digest}]"
        },
    )
    created = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[],
        dify_docker_agent_dsl={"sha256": digest, "private": False},
        dify_docker_agent_dsl_update_existing=False,
        dify_docker_dsl_existing_app={},
    )
    forced = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[
            {"id": "app-1", "description": f"notes [catalog sha256:{digest}]"}
        ],
        dify_docker_agent_dsl={"sha256": digest, "private": False},
        dify_docker_agent_dsl_update_existing=True,
        dify_docker_dsl_existing_app={
            "id": "app-1", "description": f"notes [catalog sha256:{digest}]"
        },
    )
    private_unchanged = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[
            {"id": "app-1", "description": f"notes [catalog sha256:{digest}]"}
        ],
        dify_docker_agent_dsl={"sha256": digest, "private": True},
        dify_docker_agent_dsl_update_existing=True,
        dify_docker_dsl_existing_app={
            "id": "app-1", "description": f"notes [catalog sha256:{digest}]"
        },
    )
    assert unchanged == "False"
    assert changed == "True"
    assert created == "True"
    assert forced == "True"
    assert private_unchanged == "False"

    payload_expression = payload_task["ansible.builtin.set_fact"]["dify_docker_dsl_import_body"]
    update_payload = ast.literal_eval(
        environment.from_string(payload_expression).render(
            dify_docker_dsl_content="version: 0.1.0",
            dify_docker_dsl_catalog_description="Catalog description",
            dify_docker_dsl_existing_app={"id": "app-1"},
        )
    )
    create_payload = ast.literal_eval(
        environment.from_string(payload_expression).render(
            dify_docker_dsl_content="version: 0.1.0",
            dify_docker_dsl_catalog_description="Catalog description",
            dify_docker_dsl_existing_app={},
        )
    )
    assert update_payload["app_id"] == "app-1"
    assert update_payload["description"] == "Catalog description"
    assert "catalog sha256:" not in update_payload["description"]
    assert "app_id" not in create_payload
    assert create_payload["description"] == "Catalog description"
    assert import_task["ansible.builtin.uri"]["body"] == "{{ dify_docker_dsl_import_body }}"


def test_update_mode_imports_selected_existing_catalog_apps_and_verifies_publication():
    tasks = _yaml(IMPORT_LOOP)
    importer = _expanded_yaml(IMPORT_ONE)
    payload_task = _task(importer, "Prepare the Dify import payload")
    import_task = _task(importer, "Import the catalog DSL when reconciliation requires it")
    confirm_task = _task(importer, "Confirm the agent DSL import (DSL version mismatch)")
    resolve_id_task = _task(importer, "Resolve the Dify app id returned by the catalog import")
    publish_task = _task(importer, "Publish the changed catalog workflow for API callers")
    published_workflow = _task(importer, "Read the published catalog workflow for update verification")
    preserve_id = _task(importer, "Verify the existing catalog app id is unchanged")
    select_llm_nodes = _task(importer, "Select the published catalog LLM nodes")
    verify_context = _task(importer, "Verify every published catalog LLM node has context")
    catalog_loop = _task(tasks, "Reconcile each catalog agent DSL")
    private_loop = _task(tasks, "Reconcile each private agent DSL")
    build_catalog = _task(tasks, "Build agent DSL URLs from every catalog manifest entry")

    assert import_task["ansible.builtin.uri"]["url"] == "{{ dify_docker_console_api_base }}/apps/imports"
    assert import_task["ansible.builtin.uri"]["method"] == "POST"
    assert import_task["ansible.builtin.uri"]["body"] == "{{ dify_docker_dsl_import_body }}"
    assert "'app_id': dify_docker_dsl_existing_app.id" in str(
        payload_task["ansible.builtin.set_fact"]["dify_docker_dsl_import_body"]
    )
    assert "dify_docker_dsl_import_needed | bool" in import_task["when"]
    assert import_task["no_log"] is True
    assert confirm_task["ansible.builtin.uri"]["url"].endswith(
        "/apps/imports/{{ dify_docker_dsl_import.json.id }}/confirm"
    )
    assert any("pending" in condition for condition in confirm_task["when"])
    assert confirm_task["no_log"] is True
    assert "dify_docker_dsl_confirm.json" in resolve_id_task["ansible.builtin.set_fact"][
        "dify_docker_dsl_imported_app_id"
    ]
    assert publish_task["ansible.builtin.uri"]["method"] == "POST"
    assert importer.index(confirm_task) < importer.index(publish_task)
    assert importer.index(resolve_id_task) < importer.index(publish_task)
    assert importer.index(publish_task) < importer.index(published_workflow)
    assert published_workflow["ansible.builtin.uri"]["method"] == "GET"
    assert published_workflow["ansible.builtin.uri"]["url"].endswith(
        "/apps/{{ dify_docker_dsl_imported_app_id }}/workflows/publish"
    )
    assert published_workflow["no_log"] is True
    assert "dify_docker_dsl_existing_app.id" in str(preserve_id["ansible.builtin.assert"]["that"])
    assert "dify_docker_dsl_import.json" in str(preserve_id["ansible.builtin.assert"]["that"])
    assert "dify_docker_dsl_confirm.json" in str(preserve_id["ansible.builtin.assert"]["that"])
    assert "{{ dify_docker_dsl_name }}" in preserve_id["ansible.builtin.assert"]["fail_msg"]
    assert "data.type" in select_llm_nodes["ansible.builtin.set_fact"]["dify_docker_dsl_published_llm_nodes"]
    assert "data.context" in str(verify_context["ansible.builtin.assert"]["that"])
    assert "{{ dify_docker_dsl_name }}" in verify_context["ansible.builtin.assert"]["fail_msg"]
    assert catalog_loop["loop"] == "{{ dify_docker_agent_dsls }}"
    assert private_loop["loop"].startswith("{{ dify_docker_private_agent_dsls")
    assert build_catalog["loop"] == "{{ dify_docker_agent_dsl_manifest_content | from_json }}"
    assert "dify_docker_agent_dsl_select" in str(build_catalog["when"])

    environment = Environment()
    nodes_expression = select_llm_nodes["ansible.builtin.set_fact"]["dify_docker_dsl_published_llm_nodes"]
    llm_nodes = ast.literal_eval(
        environment.from_string(nodes_expression).render(
            dify_docker_dsl_published_workflow={
                "json": {
                    "graph": {
                        "nodes": [
                            {"data": {"type": "llm", "context": {}}},
                            {"data": {"type": "end"}},
                        ]
                    }
                }
            }
        )
    )
    assert len(llm_nodes) == 1
    context_check = environment.from_string(
        "{{ " + verify_context["ansible.builtin.assert"]["that"][1] + " }}"
    )
    assert context_check.render(dify_docker_dsl_published_llm_nodes=llm_nodes) == "True"
    assert context_check.render(
        dify_docker_dsl_published_llm_nodes=[{"data": {"type": "llm"}}]
    ) == "False"
    assert context_check.render(dify_docker_dsl_published_llm_nodes=[]) == "True"
    graph_check = environment.from_string(
        "{{ " + verify_context["ansible.builtin.assert"]["that"][0] + " }}"
    )
    assert graph_check.render(
        dify_docker_dsl_published_workflow={"json": {"graph": {"nodes": [{"data": {"type": "code"}}]}}}
    ) == "True"
    assert graph_check.render(
        dify_docker_dsl_published_workflow={"json": {"graph": {"nodes": []}}}
    ) == "False"

    response_id_check = environment.from_string(
        "{{ " + preserve_id["ansible.builtin.assert"]["that"][1] + " }}"
    )
    assert response_id_check.render(
        dify_docker_dsl_confirm={"json": {"app_id": "app-1"}},
        dify_docker_dsl_import={"json": {"app_id": "app-1"}},
        dify_docker_dsl_existing_app={"id": "app-1"},
    ) == "True"
    assert response_id_check.render(
        dify_docker_dsl_confirm={"json": {"app_id": "replacement-app"}},
        dify_docker_dsl_import={"json": {"app_id": "replacement-app"}},
        dify_docker_dsl_existing_app={"id": "app-1"},
    ) == "False"


def test_default_mode_skips_an_existing_app_with_the_current_catalog_hash():
    tasks = _expanded_yaml(IMPORT_ONE)
    decision = _task(tasks, "Decide whether the catalog app needs an import")[
        "ansible.builtin.set_fact"
    ]["dify_docker_dsl_import_needed"]
    import_task = _task(tasks, "Import the catalog DSL when reconciliation requires it")
    environment = Environment()
    environment.filters["bool"] = lambda value: value is True or str(value).lower() in (
        "true", "yes", "on", "1"
    )
    digest = "a" * 64

    result = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[
            {"id": "existing-app", "description": f"[catalog sha256:{digest}]"}
        ],
        dify_docker_agent_dsl={"sha256": digest, "private": False},
        dify_docker_agent_dsl_update_existing=False,
        dify_docker_dsl_existing_app={
            "id": "existing-app", "description": f"[catalog sha256:{digest}]"
        },
    )

    assert result == "False"
    assert "dify_docker_dsl_import_needed | bool" in import_task["when"]


def test_catalog_hash_description_is_committed_after_publish_within_dify_limit():
    importer = _expanded_yaml(IMPORT_ONE)
    description_task = _task(importer, "Prepare the catalog app description")
    import_payload_task = _task(importer, "Prepare the Dify import payload")
    record_task = _task(importer, "Record the catalog hash after publishing the workflow")
    digest = "a" * 64
    environment = Environment()

    catalog_description = environment.from_string(
        description_task["ansible.builtin.set_fact"]["dify_docker_dsl_catalog_description"]
    ).render(dify_docker_dsl_document={"app": {"description": "A" * 321}})
    assert len(catalog_description) <= 318
    assert "catalog sha256:" not in str(import_payload_task["ansible.builtin.set_fact"])

    recorded_payload = ast.literal_eval(
        environment.from_string(record_task["ansible.builtin.uri"]["body"]).render(
            dify_docker_dsl_name="catalog-app",
            dify_docker_dsl_catalog_description=catalog_description,
            dify_docker_agent_dsl={"sha256": digest},
            dify_docker_dsl_app_metadata={
                "json": {
                    "icon_type": None,
                    "icon": None,
                    "icon_background": None,
                    "use_icon_as_answer_icon": False,
                    "max_active_requests": None,
                }
            },
        )
    )
    assert recorded_payload["description"].endswith(f"[catalog sha256:{digest}]")
    assert len(recorded_payload["description"]) <= 400
    assert recorded_payload["icon"] is None
