"""Guard the managed Hindsight dependency and agent-bank boundaries.

Hindsight's plugin can try a lazy pip install when its client is absent, but
Hermes's uv venv has neither pip nor ensurepip. The role, not a guest-side
operator command, owns that dependency. These tests also pin the intended
topology: agents share one external Hindsight service while each identity has
its own bank; profiles inside an agent deliberately share that bank.
"""

from __future__ import annotations

import json
from pathlib import Path

import jinja2
from jinja2 import Environment, StrictUndefined, Undefined
import yaml

from _role_files import role_defaults, role_tasks


REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"


def _task(name: str) -> dict:
    return next(task for task in role_tasks(ROLE_ROOT) if task.get("name") == name)


def _verify_task(name: str) -> dict:
    tasks = yaml.safe_load((ROLE_ROOT / "tasks" / "verify.yml").read_text())
    return next(task for task in tasks[0]["block"] if task.get("name") == name)


def _render_hindsight_config(agent_id: str, *, include_api_key: bool = True) -> dict:
    source = (ROLE_ROOT / "templates" / "hindsight-config.json.j2").read_text()
    env = Environment(autoescape=False, undefined=StrictUndefined)
    env.filters["to_json"] = json.dumps

    def mandatory(value, message):
        if isinstance(value, Undefined) or not str(value).strip():
            raise ValueError(message)
        return value

    env.filters["mandatory"] = mandatory
    context = {
        "ansible_managed": "managed",
        "hermes_agent_memory_mode": "local_external",
        "hermes_agent_memory_api_url": "https://hindsight.example.test",
        "hermes_agent_memory_api_key": "fixture-hindsight-api-key",
        "hermes_agent_memory_bank_id": agent_id,
    }
    if not include_api_key:
        context.pop("hermes_agent_memory_api_key")
    rendered = env.from_string(source).render(**context)
    return json.loads(rendered)


def test_hindsight_client_is_managed_in_the_hermes_uv_venv() -> None:
    tasks = role_tasks(ROLE_ROOT)
    install = _task("Install the Hindsight stack into the Hermes venv")
    read_version = _task("Read the installed Hindsight client version from the Hermes venv")
    assert_pin = _task("Assert the Hermes venv has the pinned Hindsight client")

    assert "{{ hermes_agent_uv_bin }} pip install" in install["ansible.builtin.command"]["cmd"]
    assert "--python {{ hermes_agent_venv_python }}" in install["ansible.builtin.command"]["cmd"]
    assert "hindsight-client{{ hermes_agent_hindsight_client_pin }}" in install["ansible.builtin.command"]["cmd"]
    assert read_version["ansible.builtin.command"]["argv"][0] == "{{ hermes_agent_venv_python }}"
    assert "version('hindsight-client')" in read_version["ansible.builtin.command"]["argv"][2]
    assert "hermes_agent_hindsight_client_version" in str(assert_pin)
    assert "hindsight-client" not in " ".join(map(str, tasks[: tasks.index(install)]))


def test_hindsight_readiness_probe_is_read_only_and_uses_rendered_agent_config() -> None:
    probe = _verify_task("Gate — Hindsight performs a read-only recall from this agent bank")
    source = probe["ansible.builtin.command"]["argv"][2]

    assert "from hindsight_client import Hindsight" in source
    assert "hindsight/config.json" in source
    assert "client.arecall(" in source
    assert 'api_key=config["api_key"]' in source
    assert "bank_id=config[\"bank_id\"]" in source
    assert "retain" not in source.lower()


def test_agents_share_hindsight_service_but_not_memory_banks() -> None:
    defaults = role_defaults(ROLE_ROOT)
    hermes = _render_hindsight_config("hermes")
    donna = _render_hindsight_config("donna")

    assert defaults["hermes_agent_memory_bank_id"] == "{{ hermes_agent_id }}"
    assert hermes["mode"] == donna["mode"] == "local_external"
    assert hermes["api_url"] == donna["api_url"]
    assert hermes["api_key"] == donna["api_key"] == "fixture-hindsight-api-key"
    assert hermes["bank_id"] == "hermes"
    assert donna["bank_id"] == "donna"
    assert hermes["bank_id"] != donna["bank_id"]


def test_local_external_hindsight_config_requires_api_key() -> None:
    try:
        _render_hindsight_config("hermes", include_api_key=False)
    except (jinja2.UndefinedError, ValueError):
        return
    raise AssertionError("Hermes Hindsight config rendered without an API key")
