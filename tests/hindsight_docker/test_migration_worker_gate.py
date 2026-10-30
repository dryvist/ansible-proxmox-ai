"""Before migrating, reject unknown or unparseable claimed-worker identities."""

from pathlib import Path
import re

import jinja2
from jinja2.nativetypes import NativeEnvironment
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/hindsight_docker/tasks/migration_worker_gate.yml"


def _gate(stdout, rc=0, inventory=("replica-a", "replica-b")):
    tasks = yaml.safe_load(TASKS.read_text())
    assertion = next(task for task in tasks if "ansible.builtin.assert" in task)
    env = NativeEnvironment(undefined=jinja2.StrictUndefined)
    env.filters["regex_findall"] = lambda value, pattern: re.findall(pattern, value)
    env.filters["difference"] = lambda values, other: list(set(values) - set(other))
    context = {
        "hindsight_docker_migration_workers": {"stdout": stdout, "rc": rc},
        "groups": {"hindsight_group": list(inventory)},
    }
    for name in ("_worker_output", "_worker_ids", "_worker_totals"):
        context[name] = env.from_string(assertion["vars"][name]).render(**context)
    return all(env.compile_expression(expr)(**context) for expr in assertion["ansible.builtin.assert"]["that"])


@pytest.mark.parametrize(
    "output,rc,accepted",
    [
        ("No processing tasks found\n", 0, True),
        ("Processing tasks across 1 worker(s):\n\nWorker: replica-a (1 task(s))\n", 0, True),
        ("Processing tasks across 1 worker(s):\n\nWorker: stranger (1 task(s))\n", 0, False),
        ("Processing tasks across 2 worker(s):\n\nWorker: replica-a (1 task(s))\n", 0, False),
        ("Processing tasks across 1 worker(s):\n\nWorker: replica-a broken format\n", 0, False),
        ("", 0, False),
        ("No processing tasks found\n", 1, False),
    ],
)
def test_worker_status_fails_closed(output, rc, accepted):
    assert _gate(output, rc) is accepted


def test_worker_probe_uses_source_image_and_runs_before_backup():
    tasks = yaml.safe_load(TASKS.read_text())
    probe = next(task for task in tasks if task.get("register") == "hindsight_docker_migration_workers")
    argv = probe["ansible.builtin.command"]["argv"]
    assert argv[-3:] == ["{{ hindsight_docker_backup_image }}", "hindsight-admin", "worker-status"]
    outer = yaml.safe_load((TASKS.parent / "run_db_migration_drain_and_migrate.yml").read_text())
    block = next(task["block"] for task in outer if "block" in task and "rescue" in task)
    gate = next(task for task in block if task.get("ansible.builtin.include_tasks") == "migration_worker_gate.yml")
    backup = next(task for task in block if task["name"] == "Take a pre-migration database backup")
    assert block.index(gate) < block.index(backup)
