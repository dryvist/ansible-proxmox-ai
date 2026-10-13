"""The controller-side price map task must run without privilege escalation."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = REPO_ROOT / "roles/llm_router/tasks/price-map-rates.yml"


def _tasks_within(task_lists: list[list[dict]]) -> list[dict]:
    tasks = []
    for task_list in task_lists:
        for task in task_list:
            tasks.append(task)
            tasks.extend(
                _tasks_within(
                    [task.get(key, []) for key in ("block", "rescue", "always")]
                )
            )
    return tasks


def test_controller_price_map_block_disables_privilege_escalation() -> None:
    tasks = yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))
    controller_block = next(
        task
        for task in tasks
        if "block" in task and task.get("delegate_to") == "localhost"
    )

    assert controller_block.get("become") is False
    nested_tasks = _tasks_within(
        [controller_block.get(key, []) for key in ("block", "rescue", "always")]
    )
    assert all(task.get("become") is not True for task in nested_tasks)
