"""Dify upgrades back up data before draining old workers and deploying new images."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MAIN_TASKS = ROOT / "roles/dify_docker/tasks/main.yml"
UPGRADE_TASKS = ROOT / "roles/dify_docker/tasks/upgrade_safety.yml"
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
COMPOSE_TEMPLATE = ROOT / "roles/dify_docker/templates/docker-compose.yml.j2"
UPGRADE_BLOCK_NAME = "Back up and stop Dify before changing its release"


def test_upgrade_safety_is_version_gated_and_precedes_compose_deploy():
    main_tasks = yaml.safe_load(MAIN_TASKS.read_text(encoding="utf-8"))
    includes = [
        task["ansible.builtin.include_tasks"]
        for task in main_tasks
        if "ansible.builtin.include_tasks" in task
    ]

    assert includes.index("upgrade_safety.yml") < includes.index("config_and_deploy.yml")

    tasks = yaml.safe_load(UPGRADE_TASKS.read_text(encoding="utf-8"))
    upgrade_flag = next(
        task
        for task in tasks
        if task.get("name") == "Detect whether the deployed Dify image differs from the target"
    )
    comparison = upgrade_flag["ansible.builtin.set_fact"]["dify_docker_upgrade_required"]
    assert "dify_docker_deployed_api_image_probe.stdout" in comparison
    assert "dify_docker_api_image" in comparison

    upgrade_block = next(task for task in tasks if task.get("name") == UPGRADE_BLOCK_NAME)
    assert upgrade_block["when"] == "dify_docker_upgrade_required | bool"

    block_tasks = upgrade_block["block"]
    first_stop = next(
        index
        for index, task in enumerate(block_tasks)
        if (
            "drain" in task.get("name", "").lower()
            or task.get("ansible.builtin.command", {}).get("argv", [])[:3]
            == ["docker", "compose", "stop"]
        )
    )
    backup_tasks = [
        index
        for index, task in enumerate(block_tasks)
        if any(
            marker in task.get("name", "").lower()
            for marker in (
                "backup",
                "snapshot",
                "workspace",
                "archive",
                "uploaded-file",
            )
        )
        and "stop" not in task.get("name", "").lower()
        and "drain" not in task.get("name", "").lower()
    ]
    assert backup_tasks
    assert max(backup_tasks) < first_stop

    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    timeout = defaults["dify_docker_stop_timeout_seconds"]
    assert isinstance(timeout, int)
    assert 0 < timeout <= 3600

    stop_tasks = [
        task
        for task in block_tasks
        if task.get("ansible.builtin.command", {}).get("argv", [])[:3]
        == ["docker", "compose", "stop"]
    ]
    assert stop_tasks
    for task in stop_tasks:
        argv = task["ansible.builtin.command"]["argv"]
        assert "-t" in argv
        assert argv[argv.index("-t") + 1] == "{{ dify_docker_stop_timeout_seconds | string }}"

    rescue_tasks = upgrade_block.get("rescue", [])
    assert rescue_tasks
    assert any("ansible.builtin.fail" in task for task in rescue_tasks)
    assert any("community.docker.docker_compose_v2" in task for task in rescue_tasks)

    compose = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    assert "{{ dify_docker_data_dir }}/storage:/app/api/storage" in compose
    assert "dify_agent_local_sandbox_home:/home/dify" in compose
    assert "dify_agent_local_sandbox_workspace:/workspace" in compose
    assert 'ENABLE_MODULES: "backup-filesystem"' in compose
    assert 'BACKUP_FILESYSTEM_PATH: "/var/lib/weaviate/backups"' in compose

    block_text = str(block_tasks)
    assert "local_sandbox:/home/dify" in block_text
    assert "/home/dify/.snapshots" in block_text
    assert "local_sandbox:/workspace" in block_text
    assert "/v1/backups/filesystem" in block_text
