"""Pin the application seed calls to the shared converge writer token.

The text is parsed as raw YAML, so no Ansible templating runs: the assertions
compare the literal `{{ openbao_secrets_converge_writer_token }}` reference.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SEED_VARS = ROOT / "inventory/group_vars/all/openbao_secrets/00-seed-vars.yml"
SEED_CALLERS = [
    ROOT / "roles/dify_docker/tasks/storage_and_secret.yml",
    ROOT / "roles/langflow_docker/tasks/main.yml",
    ROOT / "roles/langfuse_docker/tasks/seed_secrets.yml",
    ROOT / "roles/phoenix_docker/tasks/main.yml",
]
CONVERGE_TOKEN = "{{ openbao_secrets_converge_writer_token }}"


def _walk(tasks: list) -> Iterator[dict]:
    for task in tasks:
        yield task
        yield from _walk(task.get("block", []))


def _seed_call_vars(path: Path) -> list[dict]:
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    calls = []
    for task in _walk(tasks):
        include = task.get("ansible.builtin.include_role") or {}
        if include.get("name") == "dryvist.homelab.openbao_secrets" and include.get("tasks_from") == "seed_generated":
            calls.append(task.get("vars", {}))
    return calls


def test_runner_writer_token_is_read_from_its_own_env_var():
    seed_vars = yaml.safe_load(SEED_VARS.read_text(encoding="utf-8"))
    assert seed_vars["openbao_secrets_converge_writer_token"] == (
        "{{ lookup('env', 'OPENBAO_SEED_WRITER_TOKEN') | default('', true) }}"
    )


@pytest.mark.parametrize("path", SEED_CALLERS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_seed_calls_pass_the_converge_writer_token(path: Path):
    calls = _seed_call_vars(path)
    assert calls, "expected at least one dryvist.homelab.openbao_secrets seed_generated call"
    for call_vars in calls:
        assert call_vars["openbao_secrets_seed_token"] == CONVERGE_TOKEN
        assert call_vars["openbao_secrets_seed_expected_writer_policy"] == "ansible-converge"
        assert call_vars["openbao_secrets_seed_revoke_token"] is False


@pytest.mark.parametrize("path", SEED_CALLERS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_seed_callers_never_read_the_runner_bao_token(path: Path):
    assert "BAO_TOKEN" not in path.read_text(encoding="utf-8")
