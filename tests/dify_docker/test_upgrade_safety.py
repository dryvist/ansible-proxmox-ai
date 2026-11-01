"""Dify upgrades compare every image and protect the complete backup sequence."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
TASK_DIR = ROOT / "roles/dify_docker/tasks"
MAIN_TASKS = TASK_DIR / "main.yml"
UPGRADE_TASKS = TASK_DIR / "upgrade_safety.yml"
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
COMPOSE_TEMPLATE = ROOT / "roles/dify_docker/templates/docker-compose.yml.j2"
CONFIG_TASKS = TASK_DIR / "config_and_deploy.yml"
COMPOSE_TEST = ROOT / "tests/dify_docker/test_compose_ssr_api_url.yml"
UPGRADE_BLOCK_NAME = "Back up and stop Dify before changing its release"


def _expand_task_files(tasks: list[dict], source: Path) -> list[dict]:
    expanded = []
    for task in tasks:
        include = task.get("ansible.builtin.include_tasks")
        if include:
            include_path = source.parent / include
            expanded.extend(
                _expand_task_files(yaml.safe_load(include_path.read_text(encoding="utf-8")), include_path)
            )
            continue
        result = dict(task)
        for section in ("block", "rescue", "always"):
            if section in result:
                result[section] = _expand_task_files(result[section], source)
        expanded.append(result)
    return expanded


def _upgrade_tasks() -> list[dict]:
    return _expand_task_files(yaml.safe_load(UPGRADE_TASKS.read_text(encoding="utf-8")), UPGRADE_TASKS)


def _task(tasks: list[dict], name: str) -> dict:
    return next(task for task in tasks if task.get("name") == name)


def test_all_compose_component_images_are_checked_before_compose_deploy():
    main_tasks = yaml.safe_load(MAIN_TASKS.read_text(encoding="utf-8"))
    includes = [task["ansible.builtin.include_tasks"] for task in main_tasks if "ansible.builtin.include_tasks" in task]
    assert includes.index("upgrade_safety.yml") < includes.index("config_and_deploy.yml")

    tasks = _upgrade_tasks()
    target_images = _task(tasks, "Read target images for every Dify Compose component")["ansible.builtin.set_fact"][
        "dify_docker_target_images"
    ]

    compose = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    services_section = compose.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    image_services = set(re.findall(r"^  ([A-Za-z0-9_-]+):\n    image:", services_section, re.MULTILINE))
    assert set(target_images) == image_services == {
        "agent_backend",
        "agent_ssrf_proxy",
        "api",
        "db",
        "local_sandbox",
        "nginx",
        "plugin_daemon",
        "redis",
        "sandbox",
        "ssrf_proxy",
        "weaviate",
        "web",
        "worker",
        "worker_beat",
    }
    assert target_images["api"] == "{{ dify_docker_api_image }}"
    assert target_images["worker"] == "{{ dify_docker_api_image }}"
    assert target_images["worker_beat"] == "{{ dify_docker_api_image }}"
    target_services = _task(tasks, "Set the target Dify component service names")["ansible.builtin.set_fact"]
    assert "dify_docker_target_images.keys()" in target_services["dify_docker_target_image_services"]

    image_probe = _task(tasks, "Read deployed Dify component service names and images")
    probe_argv = str(image_probe["ansible.builtin.command"]["argv"])
    assert "docker" in probe_argv and "inspect" in probe_argv
    assert "com.docker.compose.service" in probe_argv and ".Config.Image" in probe_argv

    drift = _task(tasks, "Detect any deployed Dify image that differs from its target")
    assert "dify_docker_target_images" in drift["when"]
    assert "item.split('=', 1)[1]" in drift["when"]

    upgrade = _task(tasks, "Detect whether any deployed Dify component image differs from the target")
    expression = upgrade["ansible.builtin.set_fact"]["dify_docker_upgrade_required"]
    assert "dify_docker_upgrade_image_drift" in expression
    assert "dify_docker_compose_file_stat.stat.exists" in expression
    assert "dify_docker_existing_component_containers.stdout_lines" not in expression
    assert "difference" in expression


def test_backup_preflight_and_all_live_backups_precede_drain():
    tasks = _upgrade_tasks()
    upgrade_block = _task(tasks, UPGRADE_BLOCK_NAME)
    assert upgrade_block["when"] == "dify_docker_upgrade_required | bool"
    block_tasks = upgrade_block["block"]

    first_stop = next(
        index
        for index, task in enumerate(block_tasks)
        if task.get("ansible.builtin.command", {}).get("argv", [])[:3] == ["docker", "compose", "stop"]
    )
    free_space_check = next(
        index for index, task in enumerate(block_tasks) if task.get("name") == "Require 1.5 times the estimated Dify backup size to be free"
    )
    first_backup = next(
        index for index, task in enumerate(block_tasks) if task.get("name") == "Take live PostgreSQL database backups before the Dify upgrade"
    )
    assert free_space_check < first_backup < first_stop

    postgres_size = _task(block_tasks, "Read PostgreSQL database sizes for the Dify backup estimate")
    assert "pg_database_size(current_database())" in str(postgres_size["ansible.builtin.command"])
    size_measurements = _task(block_tasks, "Measure upload and Weaviate data")
    assert size_measurements["ansible.builtin.command"]["argv"][0] == "du"
    agent_measurement = _task(block_tasks, "Measure Dify Agent home and workspace data when configured")
    assert "/home/dify" in agent_measurement["ansible.builtin.command"]["argv"]
    assert "/workspace" in agent_measurement["ansible.builtin.command"]["argv"]
    threshold = _task(block_tasks, "Require 1.5 times the estimated Dify backup size to be free")
    assert "dify_docker_upgrade_minimum_free_bytes" in str(threshold["ansible.builtin.assert"])

    live_verifications = [
        "Require non-empty PostgreSQL pre-upgrade backups",
        "Verify the Dify uploaded-file archive integrity",
        "Verify the Dify Agent home archive integrity",
        "Verify the Dify Agent workspace archive integrity",
        "Verify the Weaviate pre-upgrade archive integrity",
    ]
    assert all(block_tasks.index(_task(block_tasks, name)) < first_stop for name in live_verifications)

    weaviate_stop = block_tasks.index(_task(block_tasks, "Stop Weaviate after all pre-upgrade backups are verified"))
    cold_backup = _task(block_tasks, "Create a cold Weaviate persistence backup after stopping Weaviate")
    assert block_tasks.index(cold_backup) > weaviate_stop
    assert cold_backup["when"] == "not dify_docker_weaviate_backup_module_available | bool"

    retention = _task(block_tasks, "Delete expired Dify backup sets after the new set is verified")
    assert block_tasks.index(retention) > block_tasks.index(
        _task(block_tasks, "Verify the cold Weaviate pre-upgrade archive integrity")
    )
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert defaults["dify_docker_upgrade_backup_retention_count"] == 3

    timeout = defaults["dify_docker_stop_timeout_seconds"]
    assert isinstance(timeout, int) and 0 < timeout <= 3600
    stop_tasks = [
        task
        for task in block_tasks
        if task.get("ansible.builtin.command", {}).get("argv", [])[:3] == ["docker", "compose", "stop"]
    ]
    assert stop_tasks
    for task in stop_tasks:
        argv = task["ansible.builtin.command"]["argv"]
        assert argv[argv.index("-t") + 1] == "{{ dify_docker_stop_timeout_seconds | string }}"


def test_upgrade_rescue_restarts_the_stack_before_failing():
    upgrade_block = _task(_upgrade_tasks(), UPGRADE_BLOCK_NAME)
    rescue_tasks = upgrade_block["rescue"]
    restart = _task(rescue_tasks, "Restart the deployed Dify stack after an unsuccessful backup")
    fail = _task(rescue_tasks, "Stop the upgrade after an unsuccessful pre-upgrade backup")
    assert rescue_tasks.index(restart) < rescue_tasks.index(fail)
    assert "community.docker.docker_compose_v2" in restart
    assert "ansible.builtin.fail" in fail


def test_password_pepper_is_preserved_from_existing_compose_and_rendered_only_when_defined():
    tasks = yaml.safe_load(CONFIG_TASKS.read_text(encoding="utf-8"))
    names = [task.get("name") for task in tasks]
    assert names.index("Read the existing rendered Dify environment before replacing Compose") < names.index(
        "Keep the existing Dify PASSWORD_PEPPER value"
    ) < names.index("Render Dify docker-compose.yml")
    existing_config = _task(tasks, "Read the existing rendered Dify environment before replacing Compose")
    assert existing_config["no_log"] is True
    assert existing_config["ansible.builtin.command"]["argv"][-2:] == ["--format", "json"]
    assert _task(tasks, "Keep the existing Dify PASSWORD_PEPPER value")["no_log"] is True

    template = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    assert "{% if dify_docker_password_pepper is defined %}" in template
    assert "PASSWORD_PEPPER: {{ dify_docker_password_pepper | to_json }}" in template
    test_play = COMPOSE_TEST.read_text(encoding="utf-8")
    assert "environment.get('PASSWORD_PEPPER') is none" in test_play
    assert "fixture-password-pepper" in test_play
