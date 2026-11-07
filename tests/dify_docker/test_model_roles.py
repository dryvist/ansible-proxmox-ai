"""Dify's provider model list is derived from router chat capability roles."""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/dify_docker/defaults/main.yml"
MAIN_TASKS = ROOT / "roles/dify_docker/tasks/main.yml"
PREFLIGHT_TASKS = ROOT / "roles/dify_docker/tasks/assert-model-roles.yml"
MODEL_PROVIDER_TASKS = ROOT / "roles/dify_docker/tasks/model_provider.yml"


def _render(expression, variables):
    templar = Templar(loader=DataLoader())
    templar.available_variables = {
        key: trust_as_template(value) if isinstance(value, str) else value
        for key, value in variables.items()
    }
    return templar.template(trust_as_template(expression))


def _router_fixture():
    registry = [
        {"client_model_id": "fixture-null-window-id", "route_group": "medium", "context_window": None},
        {"client_model_id": "fixture-medium-id", "route_group": "medium", "context_window": 65536},
        {"client_model_id": "fixture-small-id", "route_group": "small", "context_window": 32768},
        {
            "client_model_id": "fixture-embedding-id",
            "route_group": "embed",
            "context_window": 8192,
            "embedding": True,
        },
        {
            "client_model_id": "fixture-ocr-id",
            "route_group": "ocr",
            "context_window": 4096,
            "serving_role": "ocr",
        },
        {"client_model_id": "fixture-default-context-id", "route_group": "local-default"},
        {"client_model_id": "fixture-no-group-id", "context_window": 4096},
    ]
    return {
        "llm_router_model_registry": registry,
        "llm_router_capability_names": ["medium", "small", "embed", "ocr", "local-default"],
        "llm_router_model_group_aliases": {
            "medium": "medium",
            "small": "small",
            "embed": "embed",
            "ocr": "ocr",
            "local-default": "local-default",
        },
        "llm_router_capability_members": [
            {"client_model_id": "fixture-null-window-id", "route_group": "medium", "context_window": None},
            {"client_model_id": "fixture-medium-id", "route_group": "medium", "context_window": 65536},
            {"client_model_id": "fixture-small-id", "route_group": "small", "context_window": 32768},
        ],
        "llm_router_role_deployments": [
            {"role": "judge", "model": "small"},
            {"role": "recorder", "model": "small"},
            {"role": "recorder-medium", "model": "medium"},
            {"role": "embedding-role", "model": "embed"},
            {"role": "ocr-role", "model": "ocr"},
        ],
    }


def test_provider_models_render_chat_roles_with_served_context_sizes():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    variables = {**defaults, **_router_fixture()}
    models = _render(defaults["dify_docker_llm_models"], variables)
    by_name = {model["name"]: model for model in models}
    source_model_ids = {
        model["client_model_id"] for model in variables["llm_router_model_registry"]
    }

    assert defaults["dify_docker_default_llm_model"] == "medium"
    assert {"medium", "small", "recorder-medium", "judge", "recorder"} <= by_name.keys()
    assert not {"embedding-role", "ocr-role"} & by_name.keys()
    assert not source_model_ids & by_name.keys()
    assert not {"gpt-oss-120b", "qwen3-coder", "hermes-4-14b"} & by_name.keys()
    defaults_text = DEFAULTS.read_text(encoding="utf-8")
    assert not any(
        model_id in defaults_text
        for model_id in ("gpt-oss-120b", "qwen3-coder", "hermes-4-14b")
    )
    assert by_name["medium"]["context_size"] == "65536"
    assert by_name["small"]["context_size"] == "32768"
    assert by_name["recorder-medium"]["context_size"] == "65536"
    assert by_name["local-default"]["context_size"] == defaults["dify_docker_default_llm_context_size"]
    assert all(model_id not in str(models) for model_id in source_model_ids)


def test_empty_role_preflight_is_first_dify_action_and_has_clear_failure():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    main_tasks = yaml.safe_load(MAIN_TASKS.read_text(encoding="utf-8"))
    preflight_tasks = yaml.safe_load(PREFLIGHT_TASKS.read_text(encoding="utf-8"))
    provider_tasks = yaml.safe_load(MODEL_PROVIDER_TASKS.read_text(encoding="utf-8"))
    names = [task.get("name") for task in main_tasks]
    preflight_index = names.index("Require router chat roles before Dify tasks")

    assert names.index("Load the fabric model registry") < preflight_index
    assert preflight_index < names.index("Data directories and the durable SECRET_KEY")
    assert preflight_index < names.index("Headless first-run setup and console login")
    assert preflight_index < names.index("Model-provider plugin install and credential provisioning")
    assertion = preflight_tasks[0]["ansible.builtin.assert"]
    assert "dify_docker_llm_models | default([], true) | length > 0" in assertion["that"]
    assert "dify_docker_llm_models is empty" in assertion["fail_msg"]
    assert "mandatory(" in defaults["dify_docker_model_api_key"]
    assert all(
        task.get("ansible.builtin.uri", {}).get("body", {}).get("model") == "{{ item.name }}"
        for task in provider_tasks
        if task.get("name") in {"Create missing Dify LLM model credentials", "Update existing Dify LLM model credentials"}
    )
    assert all(
        task["ansible.builtin.uri"]["body"]["credentials"]["endpoint_model_name"]
        == "{{ item.endpoint_model_name | default(item.name) }}"
        for task in provider_tasks
        if task.get("name") in {"Create missing Dify LLM model credentials", "Update existing Dify LLM model credentials"}
    )
