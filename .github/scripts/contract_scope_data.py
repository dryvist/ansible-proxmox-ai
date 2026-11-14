"""Path-to-test mapping tables for the contract scope selector."""

from __future__ import annotations

# Ansible test files the selector can dispatch to.
ANSIBLE_TESTS = {
    "tests/repo_guards/test_env_guards_actually_fire.yml",
    "tests/openbao_secrets/test_optional_paths_are_gated.yml",
    "tests/openbao_secrets/test_gpu_profile_inventory_gate.yml",
    "tests/openbao_secrets/test_ungranted_paths_gated.yml",
    "tests/openbao_secrets/test_hermes_domain_view.yml",
    "tests/openbao_secrets/verify_hermes_api_key_seed.yml",
    "tests/phoenix_docker/test_compose_render.yml",
    "tests/hindsight_docker/test_model_var_confined_to_key_scope.yml",
    "tests/dify_docker/test_db_password.yml",
    "tests/dify_docker/test_admin_password_reset.yml",
    "tests/agent_guest/test_residual_deny_contract.yml",
    "tests/nvidia_gpu_guest/test_floor_required_for_servable.yml",
    "tests/nvidia_gpu_guest/test_cache_only_sync.yml",
    "tests/llamacpp_release/verify_inventory_proxy.yml",
    "tests/llamaindex/test_hardening.yml",
    "tests/llamaindex/test_unit_ordering.yml",
    "tests/llm_gpu_legacy/test_render_host_contract.yml",
    "tests/llm_gpu_legacy/test_render_host_contract_all.yml",
    "tests/langfuse_docker/test_stage0_evaluation.yml",
    "tests/langfuse_docker/test_code_eval_compose.yml",
}


# Removed tests have no contract; their registry cleanup is in the same diff.
RETIRED_TESTS = {"tests/langfuse_docker/test_code_eval_dispatcher.yml"}

# Exact file paths that pin a single pytest target.
PYTEST_PATH_TARGETS = {
    "roles/dify_docker/templates/docker-compose.yml.j2": "tests/agent_concurrency/test_runner_compose_caps.py",
    "roles/langflow_docker/defaults/main.yml": "tests/agent_concurrency/test_runner_compose_caps.py",
    "roles/langflow_docker/templates/docker-compose.yml.j2": "tests/agent_concurrency/test_runner_compose_caps.py",
    "tests/llm_router/fixtures/seed-key-response-shape.json": "tests/llm_router/test_seed_key_sensitivity_guard.py",
    ".github/scripts/check-installer-sha.sh": "tests/llamacpp_release/test_checksum_gate.py",
    ".github/workflows/fix-installer-sha.yml": "tests/repo_guards/test_installer_sha_workflow_race.py",
    ".github/workflows/_llm-router-contract.yml": "tests/repo_guards/test_router_contract_batches.py",
    ".github/router-contract-weights.yml": "tests/test_router_shard_weights.py",
}

# Select the OTEL test only when the checkout contains it.
OTEL_CONSUMER_TEST = "tests/hermes_agent/test_otel_endpoint_consumers.py"
OTEL_CONSUMER_ROLES = {
    "llm_router", "agent_exec", "dify_docker", "langgraph_docker", "hindsight_docker",
    "hermes_agent", "open_webui", "agentgateway_docker",
}

# Pytest directories covering the shared GPU roles.
GPU_PYTEST = {
    "tests/nvidia_gpu_guest/", "tests/llm_gpu_engine_roles/",
    "tests/llm_gpu_legacy/", "tests/llm_gpu_serving/",
    "tests/llamacpp_serving/", "tests/vllm_serving/",
}
GPU_ANSIBLE = {test for test in ANSIBLE_TESTS if test.startswith(
    ("tests/nvidia_gpu_guest/", "tests/llm_gpu_legacy/"))}

ROLE_TESTS = {
    "hermes_agent": {"tests/hermes_agent/"},
    "fabric_watchdog": {"tests/hermes_agent/"},
    "dify_docker": {
        "tests/dify_docker/test_declared_variable_apply.py",
        "tests/dify_docker/test_declared_variable_checks.py",
        "tests/dify_docker/test_declared_variable_config.py",
        "tests/dify_docker/test_declared_variable_provider_nodes.py",
    },
    "langflow_docker": {"tests/agent_concurrency/", "tests/langflow_docker/"},
    "langfuse_docker": {"tests/langfuse_docker/"},
    "hindsight_docker": {"tests/hindsight_docker/"},
    "hindsight_bank_dr": {"tests/hindsight_bank_dr/"},
    "llama_cpp": {"tests/llama_cpp/"},
    "llamacpp_release": {"tests/llamacpp_release/", "tests/llamacpp_serving/"},
    "llamacpp_serving": GPU_PYTEST,
    "vllm_serving": GPU_PYTEST,
    "strata_serving": GPU_PYTEST,
    "nvidia_gpu_guest": GPU_PYTEST,
    "llm_gpu_serving": GPU_PYTEST,
    "llm_serving_declared_metrics": GPU_PYTEST,
    "llm_model_trial": {"tests/llm_model_trial/"},
    "phoenix_docker": set(),
    "llamaindex": set(),
    "qdrant_docker": {"tests/qdrant_docker/"},
    "crawl4ai_docker": {"tests/crawl4ai_docker/"},
    "agent_guest": set(),
    "clickhouse_docker": {"tests/clickhouse_docker/", "tests/langfuse_docker/test_clickhouse_contract.py"},
    "herdr_server": set(),
    "herdr_remote": set(),
    "nixos_deploy": set(),
}
TEST_SCOPES = {target.removeprefix("tests/").rstrip("/")
               for targets in ROLE_TESTS.values() for target in targets if target.endswith("/")}
TEST_SCOPES.update({"llm_router", "llm_model_campaign", "inventory_load", "repo_guards",
                    "dify_docker", "phoenix_docker", "openbao_secrets", "llamaindex", "agent_guest"})

# Model campaign playbooks and their test tree select the campaign tests
# instead of the router contract matrix.
CAMPAIGN_TEST_SCOPE = "tests/llm_model_campaign/"
CAMPAIGN_PATHS = (
    "playbooks/llm-model-campaign",
    "playbooks/llm-model-paired-campaign",
    "playbooks/tasks/llm-model-campaign",
    "playbooks/templates/llm-model-campaign",
    "playbooks/vars/llm-model-campaign",
)
