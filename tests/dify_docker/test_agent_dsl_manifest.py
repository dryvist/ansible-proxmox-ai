"""Agent DSL catalog pins and source verification."""

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from jinja2 import Environment

from _agent_dsl_shared import AGENT_DSLS, IMPORT_LOOP, IMPORT_ONE, ROOT, _expanded_yaml, _task, _yaml

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_catalog_manifest_is_pinned_to_the_release_commit_and_checksum():
    pins = _yaml(AGENT_DSLS)
    source = AGENT_DSLS.read_text(encoding="utf-8")

    assert re.fullmatch(r"[0-9a-f]{40}", pins["dify_docker_agent_dsl_commit"])
    assert re.fullmatch(r"[0-9a-f]{64}", pins["dify_docker_agent_dsl_manifest_sha256"])
    assert "dify_docker_agent_dsls" not in pins
    assert re.search(r"currentValue=v\d+\.\d+\.\d+\b", source)

    fixture = FIXTURES / f"agent-dsl-manifest-{pins['dify_docker_agent_dsl_commit']}.json"
    assert fixture.exists(), (
        "no manifest fixture for the pinned commit; run "
        "python3 scripts/update_dify_manifest_pin.py to fetch the manifest at "
        "the pinned commit, refresh the fixture, and re-pin the checksum"
    )
    recorded = fixture.read_bytes()
    assert hashlib.sha256(recorded).hexdigest() == pins["dify_docker_agent_dsl_manifest_sha256"], (
        "the pinned manifest checksum does not match the manifest bytes recorded "
        "for the pinned commit; run python3 scripts/update_dify_manifest_pin.py "
        "instead of editing either value by hand"
    )
    entries = json.loads(recorded)
    assert entries
    assert all({"name", "path", "sha256"} <= set(entry) for entry in entries)


def test_manifest_entries_keep_their_url_and_sha256_through_public_import():
    tasks = _yaml(IMPORT_LOOP)
    manifest_fetch = _task(tasks, "Fetch the pinned agent DSL catalog manifest and verify its sha256")
    manifest_check_read = _task(tasks, "Read the pinned agent DSL catalog manifest into memory for check mode")
    manifest_check_verify = _task(tasks, "Verify the agent DSL catalog manifest sha256 in check mode")
    manifest_read = _task(tasks, "Read the verified agent DSL catalog manifest")
    build_list = _task(tasks, "Build agent DSL URLs from every catalog manifest entry")
    public_fetch = _task(tasks, "Fetch and verify all catalog agent DSLs before importing any")
    public_import = _task(tasks, "Reconcile each catalog agent DSL")
    importer = _expanded_yaml(IMPORT_ONE)
    import_task = _task(importer, "Import the catalog DSL when reconciliation requires it")

    assert manifest_fetch["ansible.builtin.get_url"]["url"] == (
        "{{ dify_docker_agent_dsl_base }}/agents/dify/manifest.json"
    )
    assert manifest_fetch["ansible.builtin.get_url"]["checksum"] == (
        "sha256:{{ dify_docker_agent_dsl_manifest_sha256 }}"
    )
    assert "not ansible_check_mode" in manifest_fetch["when"]
    assert manifest_check_read["ansible.builtin.uri"]["url"] == (
        "{{ dify_docker_agent_dsl_base }}/agents/dify/manifest.json"
    )
    assert manifest_check_read["ansible.builtin.uri"]["return_content"] is True
    assert manifest_check_read["check_mode"] is False
    assert manifest_check_read["changed_when"] is False
    assert manifest_check_read["no_log"] is True
    assert "ansible_check_mode" in manifest_check_read["when"]
    assert "content | hash('sha256')" in str(manifest_check_verify["ansible.builtin.assert"]["that"])
    assert manifest_check_verify["no_log"] is True
    assert manifest_read["ansible.builtin.slurp"]["src"] == (
        "/tmp/dify-agent-dsl-manifest-{{ dify_docker_agent_dsl_commit }}.json"
    )
    assert manifest_read["register"] == "dify_docker_agent_dsl_manifest_file_read"
    assert "not ansible_check_mode" in manifest_read["when"]
    assert "dify_docker_agent_dsl_manifest_content | from_json" in build_list["loop"]
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
    assert "not ansible_check_mode" in public_fetch["when"]
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
