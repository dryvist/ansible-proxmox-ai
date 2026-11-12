"""Dify marketplace tool plugins are pinned and credentials are read from OpenBao."""

from pathlib import Path

import yaml

from _defaults import load_defaults


ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "roles/dify_docker/tasks/main.yml"
TASKS = ROOT / "roles/dify_docker/tasks/tool_provider_one.yml"


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def test_github_provider_pin_and_read_only_credential_source_are_declared():
    provider, = load_defaults()["dify_docker_agent_tool_provider_plugins"]

    assert provider == {
        "plugin_id": "langgenius/github",
        "plugin_version": "0.3.11",
        "provider_id": "langgenius/github/github",
        "credential_name": "github-read-only",
        "credential_type": "api-key",
        "credential_visibility": "all_team_members",
        "openbao_path": "{{ dify_docker_openbao_path }}",
        "openbao_field": "GITHUB_READ_ONLY_TOKEN",
    }


def test_provider_reconciliation_runs_after_the_session_backed_model_plugin():
    tasks = _yaml(MAIN)
    names = [task.get("name") for task in tasks]
    include = _task(tasks, "Install and credential declared built-in tool providers")

    assert include["ansible.builtin.include_tasks"] == "tool_provider_one.yml"
    assert include["loop"] == "{{ dify_docker_agent_tool_provider_plugins }}"
    assert include["loop_control"]["loop_var"] == "dify_docker_agent_tool_provider"
    assert include["when"] == "dify_docker_provision_model_provider | bool"
    assert names.index("Model-provider plugin install and credential provisioning") < names.index(
        include["name"]
    )
    assert names.index(include["name"]) < names.index(
        "Seed a starter chat app (best-effort, idempotent, non-fatal)"
    )


def test_marketplace_lookup_pins_the_version_and_resolves_the_checksum():
    tasks = _yaml(TASKS)
    lookup = _task(tasks, "Read the declared built-in tool plugin from Dify Marketplace")
    uri = lookup["ansible.builtin.uri"]

    assert uri["method"] == "GET"
    assert "/plugins/identifier?unique_identifier=" in uri["url"]
    assert "plugin_version" in uri["url"]
    assert lookup["check_mode"] is False
    assert lookup["changed_when"] is False
    assert lookup["no_log"] is True

    pin = _task(tasks, "Assert the Marketplace package matches its declared plugin pin")
    assert "json.data.version.version" in str(pin["ansible.builtin.assert"]["that"])
    assert "@ [0-9a-f]" not in str(pin["ansible.builtin.assert"]["that"])
    assert "[0-9a-f]{64}" in str(pin["ansible.builtin.assert"]["that"])


def test_console_plugin_install_and_upgrade_are_skipped_in_check_mode():
    tasks = _yaml(TASKS)
    install = _task(tasks, "Install the pinned tool plugin from Dify Marketplace")
    upgrade = _task(tasks, "Upgrade the installed tool plugin to its declared pin")
    preview = _task(tasks, "Report the declared tool plugin change in check mode")

    assert install["ansible.builtin.uri"]["url"].endswith("/plugin/install/marketplace")
    assert install["when"][0] == "not ansible_check_mode"
    assert install["no_log"] is True
    assert upgrade["ansible.builtin.uri"]["url"].endswith("/plugin/upgrade/marketplace")
    assert upgrade["when"][0] == "not ansible_check_mode"
    assert upgrade["no_log"] is True
    assert preview["when"] == "ansible_check_mode"
    assert "changed_when" in preview
    assert "already has" in preview["ansible.builtin.debug"]["msg"]


def test_provider_credentials_are_team_visible_and_only_added_when_absent():
    tasks = _yaml(TASKS)
    read = _task(tasks, "Read the declared tool provider credential from OpenBao")
    add = _task(tasks, "Add the read-only credential to the declared tool provider")
    preview = _task(tasks, "Report the declared tool provider credential change in check mode")

    assert "community.hashi_vault.vault_kv2_get" in read
    assert read["community.hashi_vault.vault_kv2_get"]["path"] == (
        "{{ dify_docker_agent_tool_provider.openbao_path }}"
    )
    assert read["check_mode"] is False
    assert read["no_log"] is True
    assert add["ansible.builtin.uri"]["method"] == "POST"
    assert add["ansible.builtin.uri"]["body"]["credentials"]["access_tokens"].endswith(
        "[dify_docker_agent_tool_provider.openbao_field] }}"
    )
    assert add["ansible.builtin.uri"]["body"]["visibility"] == (
        "{{ dify_docker_agent_tool_provider.credential_visibility }}"
    )
    assert add["when"][0] == "not ansible_check_mode"
    assert add["no_log"] is True
    assert preview["when"][0] == "ansible_check_mode"
    assert "changed_when" in preview


def test_every_read_only_console_post_remains_available_in_check_mode():
    for task in _yaml(TASKS):
        uri = task.get("ansible.builtin.uri", {})
        if uri.get("method") == "POST" and task.get("changed_when") is False:
            assert task.get("check_mode") is False, task.get("name")
