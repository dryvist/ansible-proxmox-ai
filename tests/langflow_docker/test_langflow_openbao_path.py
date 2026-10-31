"""The LangFlow secret path is a role variable that defaults to the production path."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/langflow_docker/defaults/main.yml"
TASKS = ROOT / "roles/langflow_docker/tasks/main.yml"
SEED_TASK = "Seed the LangFlow secrets through the shared seed_generated task"


def test_default_path_is_the_production_path():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))

    assert defaults["langflow_docker_openbao_path"] == "ai/langflow"


def test_seed_task_reads_the_path_variable():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    seed = next(task for task in tasks if task.get("name") == SEED_TASK)
    seed_vars = seed["block"][0]["vars"]

    assert seed_vars["openbao_secrets_seed_path"] == "{{ langflow_docker_openbao_path }}"
