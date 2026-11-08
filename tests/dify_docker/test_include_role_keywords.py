"""Role includes must not carry task keywords that ansible-core rejects on an include."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/dify_docker/tasks"
INCLUDES = {"ansible.builtin.include_role", "include_role"}


def _tasks(items):
    for task in items or []:
        if not isinstance(task, dict):
            continue
        yield task
        for section in ("block", "rescue", "always"):
            yield from _tasks(task.get(section))


def test_no_include_role_task_sets_connection():
    offenders = [
        f"{path.name}: {task.get('name', '?')}"
        for path in sorted(TASKS.glob("*.yml"))
        for task in _tasks(yaml.safe_load(path.read_text(encoding="utf-8")))
        if INCLUDES & task.keys() and "connection" in task
    ]
    assert not offenders, offenders


def test_agent_app_api_key_seed_runs_on_the_controller():
    tasks = list(_tasks(yaml.safe_load((TASKS / "ensure_agent_app_api_key.yml").read_text(encoding="utf-8"))))
    wrapper = next(
        task for task in tasks if task.get("name", "").startswith("Store the Dify agent app API key through")
    )
    assert wrapper["delegate_to"] == "localhost" and wrapper["connection"] == "local"
    assert any(INCLUDES & task.keys() for task in wrapper["block"])
