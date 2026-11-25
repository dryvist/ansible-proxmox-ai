"""Every static include_tasks/import_tasks target in a role must exist."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
KEYS = {
    "include_tasks",
    "import_tasks",
    "ansible.builtin.include_tasks",
    "ansible.builtin.import_tasks",
}


def _tasks(items):
    for item in items or []:
        if not isinstance(item, dict):
            continue
        yield item
        for nested in ("block", "rescue", "always"):
            yield from _tasks(item.get(nested))


def _targets(path):
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return
    if not isinstance(data, list):
        return
    for task in _tasks(data):
        for key in KEYS & task.keys():
            value = task[key]
            if isinstance(value, dict):
                value = value.get("file")
            if isinstance(value, str) and "{{" not in value:
                yield value


def _cases():
    for path in sorted(ROOT.glob("roles/*/tasks/**/*.yml")):
        tasks_dir = path.parents[len(path.relative_to(ROOT).parts) - 4]
        for target in _targets(path):
            yield pytest.param(path, tasks_dir, target, id=f"{path.relative_to(ROOT)}->{target}")


@pytest.mark.parametrize(("path", "tasks_dir", "target"), list(_cases()))
def test_include_target_exists(path, tasks_dir, target):
    candidates = (path.parent / target, tasks_dir / target)
    assert any(c.is_file() for c in candidates), f"{path.relative_to(ROOT)} includes missing {target}"
