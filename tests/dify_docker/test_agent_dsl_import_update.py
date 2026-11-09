"""Dify agent DSL update-in-place reconciliation."""

import ast

from jinja2 import Environment

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE, ROOT, _expanded_yaml, _task, _yaml


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
