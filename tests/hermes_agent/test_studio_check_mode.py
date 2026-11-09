"""hermes-studio's unpack-dependent tasks are gated in check mode, not real runs.

A check-mode run does not execute the file/get_url tasks that materialize the
Node prefix, the runtimes, or the bun archive, so on a first install or a
version bump the tasks that consume those artifacts would each fail on files
that do not exist yet. The gate is the unpack's own `creates:` path, statted
before the unpack; these tests pin the gate's wiring and the truth table a
check run walks.
"""

from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/hermes_agent/tasks/companion_uis.yml"
BLOCK_NAME = "Install and run hermes-studio"
NODE_STAT = "Check for the pinned Node runtime binary"
CONDITION = "not ansible_check_mode or hermes_agent_studio_node_stat.stat.exists"
GATED = (
    "Unpack the Node runtime",
    "Install the bun binary",
    "Place bun on PATH",
    "Install the pinned hermes-studio package with bun",
)
UNTOUCHED = (
    NODE_STAT,
    "Fetch the pinned Node runtime for hermes-studio (checksum-verified)",
    "Create the Node prefix",
    "Install unzip for the bun release archive",
    "Fetch the pinned bun release (checksum-verified)",
    "Check the installed hermes-studio package version",
    "Create the hermes-studio state directory",
    "Install the hermes-studio unit",
    "Enable and start hermes-studio",
)


def _studio_tasks():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    block = next(task["block"] for task in tasks if task.get("name") == BLOCK_NAME)
    return {task.get("name"): task for task in block}


def _conditions(task):
    when = task.get("when")
    if when is None:
        return []
    return [when] if isinstance(when, str) else list(when)


def test_the_node_binary_stat_watches_the_unpack_creates_guard():
    tasks = _studio_tasks()
    stat_path = tasks[NODE_STAT]["ansible.builtin.stat"]["path"]
    creates = tasks["Unpack the Node runtime"]["ansible.builtin.unarchive"]["creates"]
    assert stat_path == creates == "{{ hermes_agent_studio_node_prefix }}/bin/node"
    assert tasks[NODE_STAT]["register"] == "hermes_agent_studio_node_stat"


def test_the_stat_runs_before_every_gated_task():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    block = next(task["block"] for task in tasks if task.get("name") == BLOCK_NAME)
    names = [task.get("name") for task in block]
    stat_index = names.index(NODE_STAT)
    for name in GATED:
        assert names.index(name) > stat_index, f"{name!r} must follow the {NODE_STAT!r} stat"


def test_unpack_dependent_tasks_carry_the_check_mode_gate():
    tasks = _studio_tasks()
    for name in GATED:
        assert name in tasks, f"{name!r} must exist"
        assert CONDITION in _conditions(tasks[name]), f"{name!r} lost its check-mode gate"


def test_tasks_without_unpacked_file_inputs_stay_ungated():
    tasks = _studio_tasks()
    for name in UNTOUCHED:
        assert name in tasks, f"{name!r} must exist"
        assert CONDITION not in _conditions(tasks[name]), f"{name!r} gained the gate"


def test_the_gate_stays_open_off_check_mode_and_only_closes_without_the_binary():
    # Evaluate the condition the way Ansible does — a Jinja expression over
    # the registered stat — across every check/real × binary present/absent
    # combination the guest can present.
    evaluate = Environment(autoescape=False).compile_expression(CONDITION)
    context = {"hermes_agent_studio_node_stat": {"stat": {"exists": True}}}
    assert evaluate(ansible_check_mode=False, **context) is True
    assert evaluate(ansible_check_mode=True, **context) is True
    context = {"hermes_agent_studio_node_stat": {"stat": {"exists": False}}}
    assert evaluate(ansible_check_mode=False, **context) is True
    assert evaluate(ansible_check_mode=True, **context) is False
