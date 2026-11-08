"""Dify's provider model list is derived from router chat capability roles."""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from _defaults import load_defaults, load_defaults_text


ROOT = Path(__file__).resolve().parents[2]
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
        {"client_model_id": "fixture-null-window-id", "route_group": "fixture-medium", "context_window": None},
        {"client_model_id": "fixture-medium-id", "route_group": "fixture-medium", "context_window": 65536},
        {"client_model_id": "fixture-small-id", "route_group": "fixture-small", "context_window": 32768},
        {
            "client_model_id": "fixture-embedding-id",
            "route_group": "fixture-embed",
            "context_window": 8192,
            "embedding": True,
        },
        {
            "client_model_id": "fixture-ocr-id",
            "route_group": "fixture-ocr",
            "context_window": 4096,
            "serving_role": "ocr",
        },
        {"client_model_id": "fixture-default-context-id", "route_group": "fixture-local-default"},
        {"client_model_id": "fixture-no-group-id", "context_window": 4096},
    ]
    return {
        "llm_router_model_registry": registry,
        "llm_router_capability_names": ["medium", "small", "embed", "ocr", "local-default"],
        "llm_router_model_group_aliases": {
            "medium": "fixture-medium",
            "small": "fixture-small",
            "embed": "fixture-embed",
            "ocr": "fixture-ocr",
            "local-default": "fixture-local-default",
        },
        "llm_router_capability_members": [
            {"client_model_id": "fixture-null-window-id", "route_group": "fixture-medium", "context_window": None},
            {"client_model_id": "fixture-medium-id", "route_group": "fixture-medium", "context_window": 65536},
            {"client_model_id": "fixture-small-id", "route_group": "fixture-small", "context_window": 32768},
        ],
        "_llm_router_static_virtual_keys": [
            {"alias": "open-webui", "models": ["judge", "scope-excluded-role"]},
            {
                "alias": "dify",
                "models": [
                    "medium", "small", "recorder-medium", "recorder",
                    "local-default", "embedding-role", "ocr-role",
                ],
            },
        ],
        "llm_router_role_deployments": [
            {"role": "judge", "model": "fixture-small"},
            {"role": "recorder", "model": "fixture-small"},
            {"role": "recorder-medium", "model": "fixture-medium"},
            {"role": "scope-excluded-role", "model": "fixture-small"},
            {"role": "embedding-role", "model": "fixture-embed"},
            {"role": "ocr-role", "model": "fixture-ocr"},
        ],
    }


def test_provider_models_render_chat_roles_with_served_context_sizes():
    defaults = load_defaults()
    variables = {**defaults, **_router_fixture()}
    models = _render(defaults["dify_docker_llm_models"], variables)
    by_name = {model["name"]: model for model in models}
    source_model_ids = {
        model["client_model_id"] for model in variables["llm_router_model_registry"]
    }

    assert defaults["dify_docker_default_llm_model"] == "medium"
    assert {"medium", "small", "recorder-medium", "recorder"} <= by_name.keys()
    assert not {"judge", "scope-excluded-role"} & by_name.keys()
    assert not {"embedding-role", "ocr-role"} & by_name.keys()
    assert not source_model_ids & by_name.keys()
    assert not {"gpt-oss-120b", "qwen3-coder", "hermes-4-14b"} & by_name.keys()
    defaults_text = load_defaults_text()
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
    defaults = load_defaults()
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


def test_unlisted_provider_model_is_removed_and_listed_model_is_kept():
    provider_tasks = yaml.safe_load(MODEL_PROVIDER_TASKS.read_text(encoding="utf-8"))
    preview_task = next(
        task for task in provider_tasks if task.get("name") == "Report unlisted Dify provider models in check mode"
    )
    delete_task = next(
        task
        for task in provider_tasks
        if task.get("name") == "Remove unlisted Dify provider models and their credentials"
    )
    assert next(
        task for task in provider_tasks if task.get("name") == "Read current Dify provider models before removing unlisted entries"
    )["check_mode"] is False
    assert delete_task["ansible.builtin.uri"]["method"] == "DELETE"
    assert delete_task["ansible.builtin.uri"]["status_code"] == 204
    task_names = [task.get("name") for task in provider_tasks]
    assert task_names.index(delete_task["name"]) > task_names.index("Set Dify default LLM model")

    def variables_for(model, *, check_mode):
        return {
            "ansible_check_mode": check_mode,
            "dify_docker_provision_model_provider": True,
            "dify_docker_model_provider_api_base": "provider-api",
            "dify_docker_llm_models": [{"name": "medium"}, {"name": "small"}, {"name": "recorder-medium"}],
            "item": model,
        }

    def applies(task, model, *, check_mode):
        variables = variables_for(model, check_mode=check_mode)
        conditions = task["when"]
        if isinstance(conditions, str):
            conditions = [conditions]
        return all(_render("{{ " + condition + " }}", variables) for condition in conditions)

    stale_model = {"model": "legacy-model", "model_type": "llm"}
    listed_model = {"model": "medium", "model_type": "llm"}

    assert applies(delete_task, stale_model, check_mode=False)
    assert _render(delete_task["ansible.builtin.uri"]["url"], variables_for(stale_model, check_mode=False)) == (
        "provider-api/models?model=legacy-model&model_type=llm"
    )
    assert not applies(delete_task, listed_model, check_mode=False)
    assert not applies(delete_task, stale_model, check_mode=True)
    assert applies(preview_task, stale_model, check_mode=True)
    assert not applies(preview_task, listed_model, check_mode=True)
    embedding_model = {"model": "hand-added-embedding", "model_type": "text-embedding"}
    assert not applies(delete_task, embedding_model, check_mode=False)
    assert not applies(preview_task, embedding_model, check_mode=True)


def test_model_without_a_saved_credential_is_created_not_updated():
    # Dify answers current_credential_id: null for a model with no saved credential.
    provider_tasks = yaml.safe_load(MODEL_PROVIDER_TASKS.read_text(encoding="utf-8"))
    create_task = next(
        task for task in provider_tasks if task.get("name") == "Create missing Dify LLM model credentials"
    )
    update_task = next(
        task for task in provider_tasks if task.get("name") == "Update existing Dify LLM model credentials"
    )
    model = {"name": "medium", "context_size": "32768", "mode": "chat"}

    def applies(task, current_credential_id):
        current = {"json": {"current_credential_id": current_credential_id, "credentials": {}}}
        variables = {
            "ansible_check_mode": False,
            "dify_docker_provision_model_provider": True,
            "dify_docker_model_credentials_current": {"results": [current]},
            "dify_docker_model_index": 0,
            "dify_docker_model_endpoint_url": "fixture-endpoint",
            "item": model,
        }
        return all(_render("{{ " + condition + " }}", variables) for condition in task["when"])

    for missing in (None, ""):
        assert applies(create_task, missing)
        assert not applies(update_task, missing)
    assert not applies(create_task, "fixture-credential-id")
    assert applies(update_task, "fixture-credential-id")


def test_credential_already_in_the_model_list_is_not_a_failure():
    # Dify answers 400 "Can't add same credential" when the model list already holds the credential.
    provider_tasks = yaml.safe_load(MODEL_PROVIDER_TASKS.read_text(encoding="utf-8"))
    add_task = next(
        task for task in provider_tasks if task.get("name") == "Add Dify LLM credentials to the model list"
    )

    def failed(status, payload):
        variables = {"dify_docker_model_list_add": {"status": status, "json": payload}}
        return _render("{{ " + add_task["failed_when"] + " }}", variables)

    assert not failed(200, {"result": "success"})
    assert not failed(400, {"code": "invalid_param", "message": "Can't add same credential"})
    assert failed(400, {"code": "invalid_param", "message": "Credential not found"})
    assert failed(500, {"message": "Can't add same credential"})
    assert failed(500, None)
    assert failed(-1, None)
