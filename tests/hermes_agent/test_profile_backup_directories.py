from __future__ import annotations

from pathlib import Path

import yaml

from _role_files import role_defaults

ROLE_ROOT = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"


def test_profile_config_backup_directories_follow_profile_defaults() -> None:
    defaults = role_defaults(ROLE_ROOT)
    profile_tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "profiles.yml").read_text())
    profile_flow = yaml.safe_load(
        (ROLE_ROOT / "tasks" / "skills_and_profiles.yml").read_text()
    )

    task = next(
        task
        for task in profile_tasks
        if task.get("name", "").startswith("Ensure config backup directories for profile")
    )
    file_task = task["ansible.builtin.file"]

    profile_include = next(
        task
        for task in profile_flow
        if task.get("ansible.builtin.include_tasks") == "profiles.yml"
    )
    assert defaults["hermes_agent_profiles"]
    assert profile_include["loop"] == "{{ hermes_agent_profiles }}"
    assert "{{ item.name }}" in file_task["path"]
    assert task["loop"] == ["backups", "backups/config"]
    assert task["loop_control"]["loop_var"] == "hermes_agent_profile_backup_directory"
    assert file_task["state"] == "directory"
    assert "hermes_agent_user" in defaults
    assert file_task["owner"] == "{{ hermes_agent_user }}"
    assert file_task["group"] == "{{ hermes_agent_user }}"
    assert file_task["mode"] == "{{ hermes_agent_profile_backup_directory_mode }}"
    assert defaults["hermes_agent_profile_backup_directory_mode"] == "0750"
