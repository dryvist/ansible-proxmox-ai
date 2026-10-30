"""A migration attempt must never restart an old image, even after a partial commit."""

from pathlib import Path

import jinja2
import pytest
import yaml

ROLE = Path(__file__).resolve().parents[2] / "roles/hindsight_docker"


def _sequence():
    tasks = yaml.safe_load((ROLE / "tasks/run_db_migration_drain_and_migrate.yml").read_text())
    return next(task for task in tasks if "block" in task and "rescue" in task)


def _restart_allowed(attempted, enabled):
    rescue = _sequence()["rescue"]
    task = next(
        task for task in rescue
        if "hindsight_docker_restart_allowed" in task.get("ansible.builtin.set_fact", {})
    )
    expression = task["ansible.builtin.set_fact"]["hindsight_docker_restart_allowed"]
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = lambda value: (
        value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}
    )
    return env.from_string(expression).render(
        hindsight_docker_migration_attempted=attempted,
        hindsight_service_enabled=enabled,
    ) == "True"


@pytest.mark.parametrize(
    "attempted,enabled,restart",
    [(False, False, False), (False, True, True), (True, False, False), (True, True, False)],
)
def test_restart_requires_enabled_service_and_no_migration_attempt(attempted, enabled, restart):
    assert _restart_allowed(attempted, enabled) is restart


def test_attempt_flag_is_reset_and_marked_before_the_migration_command():
    tasks = yaml.safe_load((ROLE / "tasks/run_db_migration_drain_and_migrate.yml").read_text())
    reset = next(
        index for index, task in enumerate(tasks)
        if task.get("ansible.builtin.set_fact", {}).get("hindsight_docker_migration_attempted") is False
    )
    assert reset < tasks.index(_sequence())
    block = _sequence()["block"]
    facts = [
        (index, task["ansible.builtin.set_fact"]["hindsight_docker_migration_attempted"])
        for index, task in enumerate(block)
        if "hindsight_docker_migration_attempted" in task.get("ansible.builtin.set_fact", {})
    ]
    migrate = next(
        index for index, task in enumerate(block)
        if task.get("register") == "hindsight_docker_migration_result"
    )
    assert any(index == migrate - 1 and value is True for index, value in facts)


def test_restart_is_gated_on_restart_allowed_and_failure_is_reraised():
    rescue = _sequence()["rescue"]
    restart = next(task for task in rescue if task.get("community.docker.docker_compose_v2", {}).get("state") == "present")
    assert "hindsight_docker_restart_allowed" in str(restart.get("when"))
    assert "hindsight_docker_schema_unchanged" not in str(rescue)
    failure = rescue[-1]
    assert "ansible.builtin.fail" in failure
    assert "when" not in failure
