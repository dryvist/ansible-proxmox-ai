from __future__ import annotations

from typing import Any

import yaml
from test_profile_contract import ROLE_ROOT, _defaults, _jinja_env
from _role_files import role_tasks


COMPOSITION = "hermes_agent_soul_composition"


def test_every_soul_writer_uses_the_shared_composition() -> None:
    writers = [task for task in role_tasks(ROLE_ROOT) if _soul_destination(task)]

    assert len(writers) >= 2
    assert all(
        COMPOSITION in yaml.safe_dump(task)
        for task in writers
    )


def test_shared_composition_prefixes_once_and_preserves_existing_prefix() -> None:
    expression = _defaults()[COMPOSITION]

    def compose(soul: str) -> str:
        return _jinja_env().from_string(expression).render(
            hermes_agent_soul_base="BASE\n",
            hermes_agent_soul_text=soul,
        )

    assert compose("SOUL") == "BASE\n\nSOUL"
    assert compose("BASE\n\nSOUL") == "BASE\n\nSOUL"


def test_base_manifest_guards_fail_before_every_soul_writer() -> None:
    tasks = role_tasks(ROLE_ROOT)
    writers = [index for index, task in enumerate(tasks) if _soul_destination(task)]
    assert writers

    required_failures = (
        "missing its base entry",
        "base does not match its manifest sha256",
        "base is longer than 1,200 characters",
    )
    for message in required_failures:
        failure_indices = [
            index
            for index, task in enumerate(tasks)
            if message in yaml.safe_dump(task)
            and ("ansible.builtin.assert" in task or "ansible.builtin.fail" in task)
        ]
        assert failure_indices, f"missing fail-fast task for {message}"
        assert max(failure_indices) < min(writers), (
            f"fail-fast task for {message} runs after a SOUL.md writer"
        )


def _soul_destination(task: dict[str, Any]) -> bool:
    return any(
        isinstance(action, dict)
        and str(action.get("dest", "")).endswith("SOUL.md")
        for key, action in task.items()
        if key.startswith("ansible.builtin.")
    )
