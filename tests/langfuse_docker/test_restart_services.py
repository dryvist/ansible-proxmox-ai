"""The restart knob restarts only the named compose services, and nothing by default."""

from pathlib import Path

import yaml


ROLE = Path(__file__).resolve().parents[2] / "roles/langfuse_docker"
TASKS = yaml.safe_load((ROLE / "tasks/main.yml").read_text(encoding="utf-8"))
DEFAULTS = yaml.safe_load((ROLE / "defaults/main/00-main.yml").read_text(encoding="utf-8"))
NAMES = [task.get("name") for task in TASKS]
RESTART = TASKS[NAMES.index("Restart the requested Langfuse services")]


def test_default_restarts_nothing():
    assert DEFAULTS["langfuse_docker_restart_services"] == []
    assert RESTART["when"] == "langfuse_docker_restart_services | length > 0"


def test_restart_targets_only_the_named_services():
    module = RESTART["community.docker.docker_compose_v2"]
    assert module["state"] == "restarted"
    assert module["services"] == "{{ langfuse_docker_restart_services }}"
    assert module["project_src"] == "{{ langfuse_docker_data_dir }}"


def test_restart_runs_after_the_deploy_and_before_the_health_checks():
    deploy = NAMES.index("Deploy Langfuse via docker compose")
    restart = NAMES.index("Restart the requested Langfuse services")
    worker_health = NAMES.index("Verify Langfuse worker health endpoint responds")
    assert deploy < restart < worker_health
