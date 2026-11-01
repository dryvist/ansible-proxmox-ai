"""Dify upgrades drain old workers and back up data before deploying new images."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MAIN_TASKS = ROOT / "roles/dify_docker/tasks/main.yml"
UPGRADE_TASKS = ROOT / "roles/dify_docker/tasks/upgrade_safety.yml"
UPGRADE_BLOCK_NAME = "Drain and back up Dify before changing its release"


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

    names = [task["name"] for task in upgrade_block["block"]]
    required = (
        "Stop Dify ingress and scheduler before draining Celery workers",
        "Drain Dify workers gracefully before deploying the target version",
        "Take a complete PostgreSQL backup before the Dify upgrade",
        "Stop Weaviate before taking a consistent data backup",
        "Back up the stopped Weaviate data directory",
        "Verify the Weaviate pre-upgrade archive integrity",
    )
    assert all(name in names for name in required)
    assert names.index(required[0]) < names.index(required[1])
    assert names.index(required[1]) < names.index(required[2])
    assert names.index(required[2]) < names.index(required[3])
    assert names.index(required[3]) < names.index(required[4])
