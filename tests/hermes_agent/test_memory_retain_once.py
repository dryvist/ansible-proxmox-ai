"""A retired local memory file is sent once to its profile's memory bank first.

Built-in memory is off, so an old MEMORY.md/USER.md moves into
retired-memory/<profile>/. Before it moves, its text goes to the profile's bank
in the memory service, so nothing the agent wrote is lost to the agent. A
profile with no bank only moves its files.

Runs bare (``python3 tests/hermes_agent/test_memory_retain_once.py``) or under pytest.
"""

from __future__ import annotations

import yaml

from test_profile_contract import ROLE_ROOT

TASKS = ROLE_ROOT / "tasks"


def _tasks(name: str) -> list[dict]:
    return yaml.safe_load((TASKS / name).read_text())


def _flat(tasks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for task in tasks:
        out.append(task)
        out.extend(_flat(task.get("block", [])))
    return out


def _retire() -> list[dict]:
    return _flat(_tasks("retire_memory_files.yml"))


def _index(tasks: list[dict], module: str, needle: str) -> int:
    hits = [i for i, t in enumerate(tasks) if module in t and needle in yaml.safe_dump(t[module])]
    assert len(hits) == 1, f"expected one {module} task containing {needle!r}, found {len(hits)}"
    return hits[0]


def _when(task: dict) -> str:
    when = task.get("when", [])
    return " and ".join(when if isinstance(when, list) else [str(when)])


def test_the_file_text_is_sent_to_the_bank_before_the_file_moves() -> None:
    tasks = _retire()
    retain = _index(tasks, "ansible.builtin.uri", "/memories")
    move = _index(tasks, "ansible.builtin.command", "--no-target-directory")
    assert retain < move


def test_the_retain_request_is_one_item_with_a_stable_document_id() -> None:
    task = _retire()[_index(_retire(), "ansible.builtin.uri", "/memories")]
    uri = task["ansible.builtin.uri"]
    assert uri["method"] == "POST"
    assert "/v1/default/banks/" in uri["url"] and uri["url"].rstrip('"} ').endswith("/memories")
    assert "hermes_agent_retire_memory_bank_id" in uri["url"]
    assert "Bearer {{ hermes_agent_memory_api_key }}" in uri["headers"]["Authorization"]
    body = yaml.safe_dump(uri["body"])
    for needle in ("items", "content", "document_id", "retired-local-memory", "async"):
        assert needle in body, needle
    assert "hermes_agent_retire_memory_label" in body and "hermes_agent_retire_memory_file" in body
    assert task.get("no_log") is True
    assert task["loop_control"]["loop_var"] == "hermes_agent_retire_memory_file"


def test_the_retain_step_needs_a_bank_the_memory_service_and_a_real_run() -> None:
    task = _retire()[_index(_retire(), "ansible.builtin.uri", "/memories")]
    when = _when(task)
    assert "hermes_agent_retire_memory_bank_id" in when and "length > 0" in when
    assert "hermes_agent_memory_provider == 'hindsight'" in when
    assert "not ansible_check_mode" in when


def test_an_empty_file_is_not_sent() -> None:
    task = _retire()[_index(_retire(), "ansible.builtin.uri", "/memories")]
    assert "trim" in _when(task) and "length > 0" in _when(task)


def test_the_file_is_read_on_the_guest_before_it_is_sent() -> None:
    tasks = _retire()
    slurp = [i for i, t in enumerate(tasks) if "ansible.builtin.slurp" in t]
    assert len(slurp) == 1
    assert slurp[0] < _index(tasks, "ansible.builtin.uri", "/memories")
    assert tasks[slurp[0]].get("no_log") is True


def test_each_include_site_names_the_profiles_bank() -> None:
    default = next(
        t for t in _flat(_tasks("config_and_content.yml"))
        if t.get("ansible.builtin.include_tasks") == "retire_memory_files.yml"
    )
    assert "hermes_agent_hindsight_config_bank_id" in default["vars"]["hermes_agent_retire_memory_bank_id"]
    named = next(
        t for t in _flat(_tasks("profiles.yml"))
        if t.get("ansible.builtin.include_tasks") == "retire_memory_files.yml"
    )
    assert "item.memory_bank_id" in named["vars"]["hermes_agent_retire_memory_bank_id"]
    assert "default('')" in named["vars"]["hermes_agent_retire_memory_bank_id"]


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok", _name)
