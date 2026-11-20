from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "roles" / "next_ai_drawio_docker"


def test_compose_contract_renders_from_published_ports() -> None:
    subprocess.run(
        [
            "ansible-playbook",
            "tests/next_ai_drawio_docker/test_compose_contract.yml",
            "-i",
            "localhost,",
            "-c",
            "local",
        ],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        check=True,
    )


def test_router_model_preflight_fails_closed_without_leaking_the_key() -> None:
    tasks = (ROLE_ROOT / "tasks" / "main.yml").read_text(encoding="utf-8")
    key_assert = tasks.split("- name: Require the dedicated OpenBao LiteLLM key", 1)[1]
    key_assert = key_assert.split("- name:", 1)[0]
    models_probe = tasks.split("- name: Read the authenticated, key-authorized LiteLLM model catalog", 1)[1]
    models_probe = models_probe.split("- name:", 1)[0]
    model_menu = (ROLE_ROOT / "tasks" / "build-model-menu.yml").read_text(encoding="utf-8")

    assert "next_ai_drawio_docker_llm_api_key | trim | length > 0" in key_assert
    assert "next_ai_drawio_docker_llm_base_url }}/models" in models_probe
    assert "Authorization: \"Bearer {{ next_ai_drawio_docker_llm_api_key }}\"" in models_probe
    assert "status_code: 200" in models_probe
    assert "follow_redirects: none" in models_probe
    assert "unredirected_headers:" in models_probe
    assert "- Authorization" in models_probe
    assert "use_proxy: false" in models_probe
    assert models_probe.count("no_log: true") == 1
    assert "llm_router_primary_model" in (ROLE_ROOT / "defaults" / "main.yml").read_text(encoding="utf-8")
    assert "next_ai_drawio_docker_default_model" in model_menu
    assert "| unique | join(',')" in model_menu
    assert "no_log: true" in key_assert
    assert "no_log: true" in model_menu


def test_role_uses_only_the_existing_docker_engine_dependency() -> None:
    meta = (ROLE_ROOT / "meta" / "main.yml").read_text(encoding="utf-8")
    assert "- role: docker_engine" in meta
    assert "docker_engine_configure_daemon_json: false" in meta
    assert "- community.docker" in meta


def test_inventory_prefetch_and_play_target_the_tagged_service() -> None:
    groups = (REPO_ROOT / "inventory/load_tofu/add_lxc_host_groups.yml.j2").read_text(encoding="utf-8")
    sites = (REPO_ROOT / "playbooks/site.yml").read_text(encoding="utf-8")
    orchestration = (REPO_ROOT / "playbooks/ai-orchestration.yml").read_text(encoding="utf-8")
    domains = (REPO_ROOT / "inventory/group_vars/all/openbao_secrets/10-domains.yml").read_text(encoding="utf-8")

    assert "['next_ai_drawio_group', 'ai_orchestration_group']" in groups
    assert "if 'next-ai-drawio' in (item.value.tags | default([]))" in groups
    assert "hosts: managed_secrets_group:clickhouse_group:crawl4ai_group:next_ai_drawio_group" in sites
    assert "hosts: next_ai_drawio_group" in orchestration
    assert "name: next_ai_drawio_docker" in orchestration
    assert "path: apps/next-ai-drawio, optional: true" in domains


def test_http_mcp_bridge_does_not_receive_the_router_key() -> None:
    compose = (ROLE_ROOT / "templates" / "docker-compose.yml.j2").read_text(encoding="utf-8")
    tasks = (ROLE_ROOT / "tasks" / "main.yml").read_text(encoding="utf-8")
    router_mcp = (
        REPO_ROOT / "roles" / "llm_router" / "templates" / "mcp-servers.yaml.j2"
    ).read_text(encoding="utf-8")
    router_env = (
        REPO_ROOT / "roles" / "llm_router" / "templates" / "litellm.env.j2"
    ).read_text(encoding="utf-8")

    assert "SUPERGATEWAY_API_KEY" not in compose
    assert "NEXT_AI_DRAWIO_LLM_ROUTER_KEY" in compose
    assert "auth_type: none" in router_mcp
    assert "auth_value" not in router_mcp
    assert "static_headers" not in router_mcp
    assert "LLM_ROUTER_NEXT_AI_DRAWIO_MCP_API_KEY" not in router_env
    assert "public: true" in tasks
