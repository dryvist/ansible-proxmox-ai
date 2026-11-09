"""The Dify setup check waits for the console API before reading setup state."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/dify_docker/tasks/setup_and_login.yml"
SETUP_CHECK_TASK = "Check whether Dify setup is finished"


def test_setup_check_waits_for_the_console_api():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    check = next(task for task in tasks if task.get("name") == SETUP_CHECK_TASK)

    assert check["retries"] >= 30
    assert check["delay"] >= 5
    assert "dify_docker_setup_state.status" in check["until"]
    assert check["until"].endswith("== 200")
