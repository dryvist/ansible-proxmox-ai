"""The Hermes model-catalog cache directory is claimed for the runtime user.

The cache is a child of HERMES_HOME. Ensuring HERMES_HOME alone leaves a child
directory created under another owner in place, and the catalog write then
fails with EACCES on every refresh. This pins the task that claims the child
directory, and its place after the HERMES_HOME task.

Runs bare (`python3 tests/hermes_agent/test_hermes_cache_dir_owner.py`) or under
pytest.
"""
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = REPO_ROOT / "roles" / "hermes_agent" / "tasks" / "venv_extras_and_users.yml"
HOME_TASK = "Ensure HERMES_HOME exists"
CACHE_TASK = "Ensure the Hermes cache directory is owned by the runtime user"


def _tasks():
    return yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))


def _named(name):
    matches = [task for task in _tasks() if task.get("name") == name]
    assert len(matches) == 1, f"expected one task named {name!r}, found {len(matches)}"
    return matches[0]


def test_cache_task_targets_the_cache_child_of_hermes_home():
    args = _named(CACHE_TASK)["ansible.builtin.file"]

    assert args["path"] == "{{ hermes_agent_hermes_home }}/cache"


def test_cache_task_is_a_directory_owned_by_the_runtime_user():
    args = _named(CACHE_TASK)["ansible.builtin.file"]

    assert args["state"] == "directory"
    assert args["owner"] == "{{ hermes_agent_user }}"
    assert args["group"] == "{{ hermes_agent_user }}"
    assert args["mode"] == "0700"


def test_cache_task_runs_after_the_hermes_home_task():
    names = [task.get("name") for task in _tasks()]

    assert names.index(CACHE_TASK) == names.index(HOME_TASK) + 1


if __name__ == "__main__":
    for test in (
        test_cache_task_targets_the_cache_child_of_hermes_home,
        test_cache_task_is_a_directory_owned_by_the_runtime_user,
        test_cache_task_runs_after_the_hermes_home_task,
    ):
        test()
        print(f"PASS {test.__name__}")
