"""Redis AOF conversion gates every Compose replacement on durable rewrite state."""

from pathlib import Path

import jinja2
import pytest
import yaml


ROLE = Path(__file__).resolve().parents[2] / "roles/dify_docker"
TASKS = yaml.safe_load((ROLE / "tasks/redis_persistence.yml").read_text())
CONVERSION = TASKS[-1]["block"]
ENV = jinja2.Environment(undefined=jinja2.StrictUndefined)


def test_conversion_precedes_upgrade_stop_and_compose_render():
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    includes = [task.get("ansible.builtin.include_tasks") for task in tasks]
    assert includes.index("redis_persistence.yml") < includes.index("upgrade_safety.yml")
    assert includes.index("redis_persistence.yml") < includes.index("config_and_deploy.yml")
    assert tasks[includes.index("redis_persistence.yml")]["when"] == [
        "dify_docker_redis_appendonly | bool", "not ansible_check_mode"
    ]
    commands = [task["ansible.builtin.command"] for task in CONVERSION]
    assert commands[1]["argv"][:2] == ["docker", "start"]
    assert commands[2]["argv"][-3:] == ["CONFIG", "GET", "appendonly"]
    assert commands[3]["argv"][-4:] == ["CONFIG", "SET", "appendonly", "yes"]
    assert commands[4]["argv"][-2:] == ["INFO", "persistence"]
    assert all("recreate" not in str(command) for command in commands)


@pytest.mark.parametrize("snapshot,container,allowed", [
    (False, {}, True),
    (True, {}, False),
    (True, {"stdout": ""}, False),
    (True, {"stdout": "existing-container"}, True),
])
def test_existing_snapshot_requires_its_container(snapshot, container, allowed):
    expression = TASKS[-2]["ansible.builtin.assert"]["that"][0]
    assert bool(ENV.compile_expression(expression)(
        dify_docker_redis_snapshot={"stat": {"exists": snapshot}},
        dify_docker_redis_container=container,
    )) is allowed


@pytest.mark.parametrize("setting,enabled", [("no", True), ("yes", False)])
def test_only_enable_aof_when_disabled(setting, enabled):
    expression = CONVERSION[3]["when"]
    assert bool(ENV.compile_expression(expression)(
        dify_docker_redis_aof_config={"stdout_lines": ["appendonly", setting]},
    )) is enabled


READY = [
    "aof_enabled:1", "aof_rewrite_in_progress:0", "aof_rewrite_scheduled:0",
    "aof_last_bgrewrite_status:ok", "aof_last_write_status:ok",
]


@pytest.mark.parametrize("replacement", [None, *READY])
def test_restart_gate_rejects_incomplete_or_failed_persistence(replacement):
    lines = [line for line in READY if line != replacement]
    if replacement:
        field, value = replacement.split(":")
        lines.append(f"{field}:{'err' if value == 'ok' else 1 - int(value)}")
    gate = CONVERSION[-1]
    passed = all(ENV.compile_expression(expression)(
        dify_docker_redis_aof_persistence={"stdout_lines": lines},
    ) for expression in gate["until"])
    assert passed is (replacement is None)
    assert gate["retries"] > 0 and gate["delay"] > 0
    assert "failed_when" not in gate
    assert "ignore_errors" not in gate


def test_redis_auth_stays_out_of_command_arguments_and_logs():
    for task in CONVERSION[2:]:
        assert task["environment"] == {"REDISCLI_AUTH": "{{ dify_docker_redis_password }}"}
        assert "dify_docker_redis_password" not in str(task["ansible.builtin.command"])
        assert task["no_log"] is True
