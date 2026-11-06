"""Langflow shares one local router key with its OpenAI-compatible clients."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = ROOT / "roles/langflow_docker/defaults/main.yml"
COMPOSE = ROOT / "roles/langflow_docker/templates/docker-compose.yml.j2"
TASKS = ROOT / "roles/langflow_docker/tasks/main.yml"
VIRTUAL_KEYS = ROOT / "roles/llm_router/defaults/main/56-static-virtual-keys.yml"
SECRET_DOMAINS = ROOT / "inventory/group_vars/all/openbao_secrets/10-domains.yml"


def test_langflow_router_key_is_local_and_scoped_to_primary():
    defaults = yaml.safe_load(VIRTUAL_KEYS.read_text(encoding="utf-8"))
    key = next(item for item in defaults["_llm_router_static_virtual_keys"] if item["alias"] == "langflow")

    assert key["value"] == "{{ bao_apps_secrets.langflow_llm_router_key | default('', true) }}"
    assert key["allowed_classes"] == ["local"]
    assert key["models"] == "{{ [llm_router_primary_model] }}"
    assert key["models_authoritative"] is True


def test_langflow_role_uses_the_shared_router_endpoint_and_key_as_globals():
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    compose = COMPOSE.read_text(encoding="utf-8")

    assert defaults["langflow_docker_llm_base_url"] == "{{ ai_orchestration_ollama_base_url }}"
    assert "bao_apps_secrets.langflow_llm_router_key" in defaults["langflow_docker_llm_api_key"]
    assert 'OPENAI_BASE_URL: "{{ langflow_docker_llm_base_url }}"' in compose
    assert 'OPENAI_API_KEY: "{{ langflow_docker_llm_api_key }}"' in compose
    assert 'OPENAI_COMPATIBLE_BASE_URL: "{{ langflow_docker_llm_base_url }}"' in compose
    assert 'OPENAI_COMPATIBLE_API_KEY: "{{ langflow_docker_llm_api_key }}"' in compose
    assert (
        'LANGFLOW_VARIABLES_TO_GET_FROM_ENVIRONMENT: "OPENAI_BASE_URL,OPENAI_API_KEY,'
        'OPENAI_COMPATIBLE_BASE_URL,OPENAI_COMPATIBLE_API_KEY"'
    ) in compose
    assert 'LANGFLOW_STORE_ENVIRONMENT_VARIABLES: "True"' in compose
    assert 'LANGFLOW_FALLBACK_TO_ENV_VAR: "True"' in compose


def test_langflow_router_secret_path_is_fetched():
    domains = yaml.safe_load(SECRET_DOMAINS.read_text(encoding="utf-8"))
    apps = next(
        domain
        for domain in domains["_openbao_secrets_static_domains_fleet"]
        if domain["name"] == "apps"
    )

    assert {"path": "apps/langflow", "optional": True} in apps["paths"]


def test_router_key_is_required_before_writing_the_compose_file():
    tasks = yaml.safe_load(TASKS.read_text(encoding="utf-8"))
    check = next(
        task
        for task in tasks
        if task["name"] == "Assert every secret rendered into the LangFlow compose file is present"
    )

    assert "langflow_docker_llm_api_key" in check["vars"]["openbao_secrets_required_vars"]
