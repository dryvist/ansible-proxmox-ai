"""The converge reads back the base text, the memory switches and the memory files.

Offline tests prove what the templates render. This file holds the read-back
itself to its contract: it only reads, it reads in check mode too, no file
content reaches the log, and each of the three gates names what it compares.

Runs bare (``python3 tests/hermes_agent/test_verify_base_and_memory.py``) or under pytest.
"""

from __future__ import annotations

import yaml

from test_profile_contract import ROLE_ROOT

TASKS = ROLE_ROOT / "tasks" / "verify_base_and_memory.yml"
READ_ONLY = {
    "ansible.builtin.find",
    "ansible.builtin.slurp",
    "ansible.builtin.set_fact",
    "ansible.builtin.assert",
    "ansible.builtin.debug",
}


def _tasks() -> list[dict]:
    return yaml.safe_load(TASKS.read_text())


def _module(task: dict) -> str:
    return next(key for key in task if key.startswith("ansible.builtin."))


def test_verify_includes_the_read_back() -> None:
    assert "verify_base_and_memory.yml" in (ROLE_ROOT / "tasks" / "verify.yml").read_text()


def test_the_read_back_only_reads() -> None:
    for task in _tasks():
        assert _module(task) in READ_ONLY, task["name"]


def test_every_remote_read_also_runs_in_check_mode() -> None:
    for task in _tasks():
        if _module(task) in {"ansible.builtin.find", "ansible.builtin.slurp"}:
            assert task.get("check_mode") is False, task["name"]


def test_file_content_never_reaches_the_log() -> None:
    for task in _tasks():
        if _module(task) in {"ansible.builtin.slurp", "ansible.builtin.set_fact"}:
            assert task.get("no_log") is True, task["name"]


def test_no_gate_is_allowed_to_fail_quietly() -> None:
    for task in _tasks():
        assert "ignore_errors" not in task and "failed_when" not in task, task["name"]


def test_the_three_gates_name_what_they_compare() -> None:
    text = TASKS.read_text()
    gates = [task for task in _tasks() if _module(task) == "ansible.builtin.assert"]
    assert len(gates) == 3
    for gate in gates:
        assert gate["ansible.builtin.assert"].get("fail_msg"), gate["name"]
    for needle in (
        "hermes_agent_soul_base",
        "memory_enabled",
        "user_profile_enabled",
        "retired-memory",
        "MEMORY.md",
        "USER.md",
    ):
        assert needle in text, needle


if __name__ == "__main__":
    for name, test in sorted(globals().items()):
        if name.startswith("test_"):
            test()
    print("ok")
