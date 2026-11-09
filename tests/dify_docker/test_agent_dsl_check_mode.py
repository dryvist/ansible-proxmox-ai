"""Dify agent DSL check-mode previews and read boundaries."""

from _agent_dsl_shared import IMPORT_LOOP, IMPORT_ONE, _expanded_yaml, _task, _yaml


def test_check_mode_previews_catalog_without_importing_or_provisioning_keys():
    importer = _expanded_yaml(IMPORT_ONE)
    import_task = _task(importer, "Import the catalog DSL when reconciliation requires it")
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


def test_public_check_mode_reads_manifest_in_memory_and_writes_no_target_files():
    tasks = _yaml(IMPORT_LOOP)
    importer = _expanded_yaml(IMPORT_ONE)
    manifest_read = _task(tasks, "Read the pinned agent DSL catalog manifest into memory for check mode")
    manifest_fetch = _task(tasks, "Fetch the pinned agent DSL catalog manifest and verify its sha256")
    manifest_slurp = _task(tasks, "Read the verified agent DSL catalog manifest")
    manifest_cleanup = _task(tasks, "Remove the verified agent DSL catalog manifest copy")
    public_fetch = _task(tasks, "Fetch and verify all catalog agent DSLs before importing any")
    dsl_read = _task(importer, "Read the catalog agent DSL into memory for check mode")

    assert manifest_read["ansible.builtin.uri"]["method"] == "GET"
    assert manifest_read["no_log"] is True
    assert "ansible_check_mode" in manifest_read["when"]
    for task in (manifest_fetch, manifest_slurp, manifest_cleanup, public_fetch):
        assert "not ansible_check_mode" in task["when"], task["name"]
    assert dsl_read["ansible.builtin.uri"]["method"] == "GET"
    assert dsl_read["ansible.builtin.uri"]["return_content"] is True
    assert dsl_read["no_log"] is True

    # All target filesystem changes and remote API writes in these import files
    # are gated off in check mode. GET requests may read their pinned sources.
    target_mutating_modules = (
        "ansible.builtin.get_url",
        "ansible.builtin.file",
        "ansible.builtin.copy",
        "ansible.builtin.template",
        "ansible.builtin.lineinfile",
        "ansible.builtin.blockinfile",
        "ansible.builtin.replace",
        "ansible.builtin.assemble",
        "ansible.builtin.unarchive",
        "ansible.builtin.synchronize",
        "ansible.builtin.command",
        "ansible.builtin.shell",
    )
    for task in [*tasks, *importer]:
        mutates_filesystem = any(module in task for module in target_mutating_modules)
        uri = task.get("ansible.builtin.uri", {})
        writes_remote_api = uri.get("method", "GET") not in ("GET", "HEAD", "OPTIONS")
        assert not ("dest" in uri), task.get("name")
        if mutates_filesystem or writes_remote_api:
            assert "not ansible_check_mode" in task.get("when", []), task.get("name")


def test_check_mode_uses_separate_source_registers_before_selecting_dsl_content():
    importer = _expanded_yaml(IMPORT_ONE)
    source_read = _task(importer, "Read the catalog agent DSL into memory for check mode")
    file_read = _task(importer, "Read the verified agent DSL")
    selection = _task(importer, "Select the verified in-memory or file-backed agent DSL")
    parse = _task(importer, "Parse the verified DSL document and app name")

    assert source_read["register"] == "dify_docker_dsl_check_read"
    assert file_read["register"] == "dify_docker_dsl_file_read"
    assert source_read["register"] != file_read["register"]
    assert "ansible_check_mode" in source_read["when"]
    assert "not ansible_check_mode" in file_read["when"]
    assert "dify_docker_dsl_check_read.content | b64encode" in str(
        selection["ansible.builtin.set_fact"]["dify_docker_dsl_raw"]
    )
    assert "dify_docker_dsl_file_read.content" in str(
        selection["ansible.builtin.set_fact"]["dify_docker_dsl_raw"]
    )
    assert selection["no_log"] is True
    assert "dify_docker_dsl_raw.content" in str(parse["ansible.builtin.set_fact"])
    assert parse["no_log"] is True


def test_private_catalog_entries_are_included_in_check_mode_preview():
    private = _task(_yaml(IMPORT_LOOP), "Reconcile each private agent DSL")
    public = _task(_yaml(IMPORT_LOOP), "Reconcile each catalog agent DSL")

    assert private["loop"] == (
        "{{ dify_docker_private_agent_dsls | map('combine', {'private': true}) | list }}"
    )
    assert "dify_docker_private_agent_dsl_token | length > 0" in private["when"]
    assert "not ansible_check_mode" not in private["when"]
    assert "not ansible_check_mode" not in public["when"]
    assert private["ansible.builtin.include_tasks"] == public["ansible.builtin.include_tasks"]
