"""LangFlow must not invent secrets after its PostgreSQL state exists."""

from pathlib import Path

from jinja2.nativetypes import NativeEnvironment
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "roles/langflow_docker/tasks/main.yml"
GENERATE = "{{ not langflow_docker_postgres_initialized.stat.exists }}"
FIELDS = {
    "LANGFLOW_SUPERUSER",
    "LANGFLOW_SUPERUSER_PASSWORD",
    "LANGFLOW_DB_PASSWORD",
    "LANGFLOW_SECRET_KEY",
}


def _tasks():
    return yaml.safe_load(TASKS.read_text(encoding="utf-8"))


def test_postgres_initialization_check_precedes_secret_seed():
    tasks = _tasks()
    names = [task.get("name") for task in tasks]
    check_index = names.index("Check whether PostgreSQL has initialized the LangFlow data directory")
    seed_index = names.index("Seed the LangFlow secrets through the shared seed_generated task")
    check = tasks[check_index]

    assert check_index < seed_index
    assert check["ansible.builtin.stat"]["path"] == "{{ langflow_docker_postgres_data_dir }}/PG_VERSION"
    assert check["register"] == "langflow_docker_postgres_initialized"
    assert check["changed_when"] is False


def test_every_secret_disables_generation_after_postgres_initializes():
    tasks = _tasks()
    seed = next(
        task for task in tasks
        if task.get("name") == "Seed the LangFlow secrets through the shared seed_generated task"
    )
    fields = seed["block"][0]["vars"]["openbao_secrets_seed_fields"]

    assert {field["name"] for field in fields} == FIELDS
    assert all(field["generate"] == GENERATE for field in fields)
    secret_key = next(field for field in fields if field["name"] == "LANGFLOW_SECRET_KEY")
    assert secret_key["value"] == "{{ langflow_docker_secret_key_caller }}"


@pytest.mark.parametrize(
    ("postgres_initialized", "generation_allowed"),
    [
        pytest.param(True, False, id="initialized-database"),
        pytest.param(False, True, id="new-database"),
    ],
)
def test_role_generation_flags_for_postgres_state(postgres_initialized, generation_allowed):
    tasks = _tasks()
    seed = next(
        task for task in tasks
        if task.get("name") == "Seed the LangFlow secrets through the shared seed_generated task"
    )
    fields = seed["block"][0]["vars"]["openbao_secrets_seed_fields"]
    context = {
        "langflow_docker_postgres_initialized": {"stat": {"exists": postgres_initialized}},
    }

    assert {field["name"] for field in fields} == FIELDS
    for field in fields:
        rendered = NativeEnvironment().from_string(field["generate"]).render(**context)
        assert rendered is generation_allowed, field["name"]
