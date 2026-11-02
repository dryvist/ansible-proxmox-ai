"""Evaluation LangFlow secrets must never resolve production values."""

from __future__ import annotations

import pytest

from _eval_support import (
    GUEST,
    LANGFLOW_EVAL_PATH,
    PRODUCTION,
    PRODUCTION_PREFETCH,
    guest_key_facts,
    host_context,
    render,
    task_named,
)

LANGFLOW_CALLER = "Resolve the caller LANGFLOW_SECRET_KEY (inventory override, then on-guest file)"
LANGFLOW_SEED = "Seed the LangFlow secrets through the shared seed_generated task"
LANGFLOW_CALLER_VARIABLES = [
    ("langflow_docker_superuser", "LANGFLOW_SUPERUSER"),
    ("langflow_docker_superuser_password", "LANGFLOW_SUPERUSER_PASSWORD"),
    ("langflow_docker_db_password", "LANGFLOW_DB_PASSWORD"),
]
LANGFLOW_IDS = [variable for variable, _ in LANGFLOW_CALLER_VARIABLES]
PREFETCH_MODES = [None, PRODUCTION_PREFETCH]
PREFETCH_IDS = ["real-context", "production-maps-injected"]


def _render_caller_langflow(context: dict) -> str:
    expression = task_named("langflow_docker", "main.yml", LANGFLOW_CALLER)["ansible.builtin.set_fact"]
    return render(expression["langflow_docker_secret_key_caller"], context)


@pytest.mark.parametrize(("variable", "production_env"), LANGFLOW_CALLER_VARIABLES, ids=LANGFLOW_IDS)
def test_langflow_caller_value_is_empty_so_the_seed_generates_it(
    variable: str, production_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(production_env, PRODUCTION)
    context = host_context("langflow_docker", "langflow_eval_group", PRODUCTION_PREFETCH)

    assert render("{{ " + variable + " }}", context) == ""


@pytest.mark.parametrize(("variable", "production_env"), LANGFLOW_CALLER_VARIABLES, ids=LANGFLOW_IDS)
def test_langflow_control_selects_production_without_the_override(
    variable: str, production_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(production_env, PRODUCTION)
    context = host_context("langflow_docker")

    assert render("{{ " + variable + " }}", context) == PRODUCTION


@pytest.mark.parametrize("prefetch", PREFETCH_MODES, ids=PREFETCH_IDS)
def test_langflow_secret_key_keeps_the_guest_key(prefetch: dict | None) -> None:
    context = {**host_context("langflow_docker", "langflow_eval_group", prefetch), **guest_key_facts()}

    assert _render_caller_langflow(context) == GUEST


def test_langflow_seed_writes_only_evaluation_values_to_the_evaluation_path(monkeypatch: pytest.MonkeyPatch) -> None:
    for _, production_env in LANGFLOW_CALLER_VARIABLES:
        monkeypatch.setenv(production_env, PRODUCTION)
    context = {**host_context("langflow_docker", "langflow_eval_group", PRODUCTION_PREFETCH), **guest_key_facts()}
    context["langflow_docker_secret_key_caller"] = _render_caller_langflow(context)
    seed_vars = task_named("langflow_docker", "main.yml", LANGFLOW_SEED)["block"][0]["vars"]
    rendered = {
        field["name"]: render(field["value"], context) for field in seed_vars["openbao_secrets_seed_fields"]
    }

    assert render(seed_vars["openbao_secrets_seed_path"], context) == LANGFLOW_EVAL_PATH
    assert rendered == {
        "LANGFLOW_SUPERUSER": "",
        "LANGFLOW_SUPERUSER_PASSWORD": "",
        "LANGFLOW_DB_PASSWORD": "",
        "LANGFLOW_SECRET_KEY": GUEST,
    }
