"""Agent DSL imports use the pinned catalog manifest and verify every source."""

import ast
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from jinja2 import Environment

from _agent_dsl_shared import _task, _yaml


ROOT = Path(__file__).resolve().parents[2]
AGENT_DSLS = ROOT / "roles/dify_docker/vars/agent_dsls.yml"
IMPORT_LOOP = ROOT / "roles/dify_docker/tasks/import_agent_dsls.yml"
IMPORT_ONE = ROOT / "roles/dify_docker/tasks/import_agent_dsl.yml"
LIST_CATALOG_PAGE = ROOT / "roles/dify_docker/tasks/list_catalog_app_page.yml"

def test_catalog_manifest_is_pinned_to_the_release_commit_and_checksum():
    pins = _yaml(AGENT_DSLS)
    source = AGENT_DSLS.read_text(encoding="utf-8")

    assert re.fullmatch(r"[0-9a-f]{40}", pins["dify_docker_agent_dsl_commit"])
    assert re.fullmatch(r"[0-9a-f]{64}", pins["dify_docker_agent_dsl_manifest_sha256"])
    assert "dify_docker_agent_dsls" not in pins
    assert "currentValue=v1.77.0" in source


def test_manifest_entries_keep_their_url_and_sha256_through_public_import():
    tasks = _yaml(IMPORT_LOOP)
    manifest_fetch = _task(tasks, "Fetch the pinned agent DSL catalog manifest and verify its sha256")
    manifest_read = _task(tasks, "Read the verified agent DSL catalog manifest")
    build_list = _task(tasks, "Build agent DSL URLs from every catalog manifest entry")
    public_fetch = _task(tasks, "Fetch and verify all catalog agent DSLs before importing any")
    public_import = _task(tasks, "Reconcile each catalog agent DSL")
    importer = _yaml(IMPORT_ONE)
    import_task = _task(importer, "Import the catalog DSL when its checksum is new")

    assert manifest_fetch["ansible.builtin.get_url"]["url"] == (
        "{{ dify_docker_agent_dsl_base }}/agents/dify/manifest.json"
    )
    assert manifest_fetch["ansible.builtin.get_url"]["checksum"] == (
        "sha256:{{ dify_docker_agent_dsl_manifest_sha256 }}"
    )
    assert manifest_read["ansible.builtin.slurp"]["src"] == (
        "/tmp/dify-agent-dsl-manifest-{{ dify_docker_agent_dsl_commit }}.json"
    )
    assert "| from_json" in build_list["loop"]
    assert "dify_docker_agent_dsl_manifest_entry.path" in build_list["ansible.builtin.set_fact"][
        "dify_docker_agent_dsls"
    ]
    assert "combine({'url':" in build_list["ansible.builtin.set_fact"]["dify_docker_agent_dsls"]
    assert "selectattr" not in build_list["loop"]
    assert "dify_docker_agent_dsl_manifest_entry.kind" not in str(build_list)
    assert "dify_docker_agent_dsl_manifest_entry.mode" not in str(build_list)

    environment = Environment()
    environment.filters["combine"] = lambda base, extra: {**base, **extra}
    driver = {
        "kind": "driver",
        "mode": None,
        "name": "tier2-rotation",
        "path": "agents/dify/tier2-rotation.dsl.yml",
        "sha256": "a" * 64,
    }
    rendered_entries = ast.literal_eval(
        environment.from_string(build_list["ansible.builtin.set_fact"]["dify_docker_agent_dsls"]).render(
            dify_docker_agent_dsls=[],
            dify_docker_agent_dsl_base="https://raw.example/catalog/commit",
            dify_docker_agent_dsl_manifest_entry=driver,
        )
    )
    assert rendered_entries == [
        {
            **driver,
            "url": "https://raw.example/catalog/commit/agents/dify/tier2-rotation.dsl.yml",
        }
    ]

    assert public_fetch["loop"] == "{{ dify_docker_agent_dsls }}"
    assert public_fetch["ansible.builtin.get_url"]["url"] == "{{ dify_docker_agent_dsl.url }}"
    assert public_fetch["ansible.builtin.get_url"]["checksum"] == (
        "sha256:{{ dify_docker_agent_dsl.sha256 }}"
    )
    assert public_import["loop"] == "{{ dify_docker_agent_dsls }}"
    assert public_import["ansible.builtin.include_tasks"] == "import_agent_dsl.yml"
    assert "dify_docker_dsl_import_needed | bool" in import_task["when"]
    assert "app_id" in str(importer)
    assert import_task["ansible.builtin.uri"]["body"] == "{{ dify_docker_dsl_import_body }}"
    assert "dify_docker_agent_dsl.mode" not in str(importer)


def test_manifest_or_public_dsl_hash_failure_stops_before_any_import(tmp_path):
    tasks = _yaml(IMPORT_LOOP)
    by_name = {task.get("name"): (index, task) for index, task in enumerate(tasks)}
    manifest_fetch_index, manifest_fetch = by_name[
        "Fetch the pinned agent DSL catalog manifest and verify its sha256"
    ]
    public_fetch_index, public_fetch = by_name[
        "Fetch and verify all catalog agent DSLs before importing any"
    ]
    apps_index, _ = by_name["List first page of existing Dify apps (agent DSL catalog hash check)"]
    public_import_index, _ = by_name["Reconcile each catalog agent DSL"]

    assert "ignore_errors" not in manifest_fetch
    assert "failed_when" not in manifest_fetch
    assert "ignore_errors" not in public_fetch
    assert "failed_when" not in public_fetch
    assert manifest_fetch_index < public_fetch_index < apps_index < public_import_index
    assert manifest_fetch["check_mode"] is False
    assert public_fetch["check_mode"] is False

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"fixture": true}\n', encoding="utf-8")
    wrong_checksum = "sha256:" + "0" * 64
    fetch_fixture = deepcopy(manifest_fetch)
    fetch_fixture.pop("no_log", None)
    fetch_fixture.pop("when", None)
    fetch_fixture["ansible.builtin.get_url"].update(
        {
            "url": manifest.as_uri(),
            "dest": str(tmp_path / "downloaded-manifest.json"),
            "checksum": wrong_checksum,
        }
    )
    environment = dict(os.environ)
    environment["ANSIBLE_LOCAL_TEMP"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "ansible.modules.get_url"],
        cwd=ROOT,
        env=environment,
        input=json.dumps({"ANSIBLE_MODULE_ARGS": fetch_fixture["ansible.builtin.get_url"]}),
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "did not match" in output, output
    assert wrong_checksum.partition(":")[2] in output, output


def test_check_mode_previews_catalog_without_importing_or_provisioning_keys():
    importer = _yaml(IMPORT_ONE)
    import_task = _task(importer, "Import the catalog DSL when its checksum is new")
    confirm_task = _task(importer, "Confirm the agent DSL import (DSL version mismatch)")
    preview_task = _task(importer, "Preview the catalog app create or update")
    publish_task = _task(importer, "Publish the changed catalog workflow for API callers")
    key_task = _task(importer, "Ensure the agent app API key is stored in OpenBao")

    assert "not ansible_check_mode" in import_task["when"]
    assert "not ansible_check_mode" in confirm_task["when"]
    assert "not ansible_check_mode" in key_task["when"]
    assert "not (dify_docker_dsl_import_needed | bool)" in key_task["when"][-1]
    assert "ansible_check_mode" in preview_task["when"]
    assert "not ansible_check_mode" in publish_task["when"]
    assert preview_task["changed_when"] is True
    assert "dify_docker_dsl_name" in preview_task["ansible.builtin.debug"]["msg"]
    assert "create" in preview_task["ansible.builtin.debug"]["msg"]
    assert "overwrite" in preview_task["ansible.builtin.debug"]["msg"]
    assert preview_task.get("no_log") is not True
    assert not any(
        secret_field in preview_task["ansible.builtin.debug"]["msg"]
        for secret_field in ("cookie", "token", "api_key", "dsl_content")
    )


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
    importer = _yaml(IMPORT_ONE)
    tasks_by_name = {task.get("name"): (index, task) for index, task in enumerate(importer)}
    _, import_task = tasks_by_name["Import the catalog DSL when its checksum is new"]
    _, confirm_task = tasks_by_name["Confirm the agent DSL import (DSL version mismatch)"]
    app_id_index, app_id_task = tasks_by_name["Resolve the Dify app id returned by the catalog import"]
    publish_index, publish_task = tasks_by_name["Publish the changed catalog workflow for API callers"]
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
    assert "not (dify_docker_dsl_import_needed | bool)" in key_task["when"][-1]

    environment = Environment()
    import_failure = environment.from_string("{{ " + import_task["failed_when"] + " }}")
    confirm_failure = environment.from_string("{{ " + confirm_task["failed_when"] + " }}")
    publish_failure = environment.from_string("{{ " + publish_task["failed_when"] + " }}")
    for completed_status in ("completed", "completed-with-warnings"):
        assert import_failure.render(dify_docker_dsl_import={"json": {"status": completed_status}}) == "False"
        assert confirm_failure.render(dify_docker_dsl_confirm={"json": {"status": completed_status}}) == "False"
    assert import_failure.render(dify_docker_dsl_import={"json": {"status": "pending"}}) == "False"
    assert import_failure.render(dify_docker_dsl_import={"json": {"status": "failed"}}) == "True"
    assert confirm_failure.render(dify_docker_dsl_confirm={"json": {"status": "failed"}}) == "True"
    assert publish_failure.render(dify_docker_dsl_publish={"json": {"result": "success"}}) == "False"
    assert publish_failure.render(dify_docker_dsl_publish={"json": {"result": "error"}}) == "True"


def test_catalog_hash_decides_whether_to_import_and_payload_targets_existing_app():
    importer = _yaml(IMPORT_ONE)
    decision_task = _task(importer, "Decide whether the catalog app needs an import")
    payload_task = _task(importer, "Prepare the Dify import payload with its catalog hash")
    import_task = _task(importer, "Import the catalog DSL when its checksum is new")
    environment = Environment()
    environment.filters["combine"] = lambda base, extra: {**base, **extra}
    digest = "a" * 64
    old_digest = "b" * 64

    decision = decision_task["ansible.builtin.set_fact"]["dify_docker_dsl_import_needed"]
    unchanged = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[
            {"id": "app-1", "description": f"notes [catalog sha256:{digest}]"}
        ],
        dify_docker_agent_dsl={"sha256": digest},
        dify_docker_dsl_existing_app={
            "id": "app-1", "description": f"notes [catalog sha256:{digest}]"
        },
    )
    changed = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[
            {"id": "app-1", "description": f"notes [catalog sha256:{old_digest}]"}
        ],
        dify_docker_agent_dsl={"sha256": digest},
        dify_docker_dsl_existing_app={
            "id": "app-1", "description": f"notes [catalog sha256:{old_digest}]"
        },
    )
    created = environment.from_string(decision).render(
        dify_docker_dsl_app_matches=[],
        dify_docker_agent_dsl={"sha256": digest},
        dify_docker_dsl_existing_app={},
    )
    assert unchanged == "False"
    assert changed == "True"
    assert created == "True"

    payload_expression = payload_task["ansible.builtin.set_fact"]["dify_docker_dsl_import_body"]
    update_payload = ast.literal_eval(
        environment.from_string(payload_expression).render(
            dify_docker_dsl_content="version: 0.1.0",
            dify_docker_dsl_document={"app": {"description": "Catalog description"}},
            dify_docker_agent_dsl={"sha256": digest},
            dify_docker_dsl_existing_app={"id": "app-1"},
        )
    )
    create_payload = ast.literal_eval(
        environment.from_string(payload_expression).render(
            dify_docker_dsl_content="version: 0.1.0",
            dify_docker_dsl_document={"app": {"description": "Catalog description"}},
            dify_docker_agent_dsl={"sha256": digest},
            dify_docker_dsl_existing_app={},
        )
    )
    assert update_payload["app_id"] == "app-1"
    assert update_payload["description"].endswith(f"[catalog sha256:{digest}]")
    assert "app_id" not in create_payload
    assert create_payload["description"].endswith(f"[catalog sha256:{digest}]")
    assert import_task["ansible.builtin.uri"]["body"] == "{{ dify_docker_dsl_import_body }}"
