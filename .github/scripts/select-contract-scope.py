#!/usr/bin/env python3
"""Select focused contract tests from an explicit changed-path mapping."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TypedDict

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
    "tests/llm_gpu_legacy/test_render_host_contract.yml",
    "tests/llm_gpu_legacy/test_render_host_contract_all.yml",
    "tests/langfuse_docker/test_stage0_evaluation.yml",
    "tests/langfuse_docker/test_code_eval_dispatcher.yml",
    "tests/langfuse_docker/test_code_eval_compose.yml",
}


PYTEST_PATH_TARGETS = {
    "roles/dify_docker/templates/docker-compose.yml.j2": "tests/agent_concurrency/test_runner_compose_caps.py",
    "roles/langflow_docker/defaults/main.yml": "tests/agent_concurrency/test_runner_compose_caps.py",
    "roles/langflow_docker/templates/docker-compose.yml.j2": "tests/agent_concurrency/test_runner_compose_caps.py",
    "tests/llm_router/fixtures/seed-key-response-shape.json": "tests/llm_router/test_seed_key_sensitivity_guard.py",
    ".github/scripts/check-installer-sha.sh": "tests/llamacpp_release/test_checksum_gate.py",
    ".github/workflows/fix-installer-sha.yml": "tests/repo_guards/test_installer_sha_workflow_race.py",
    ".github/workflows/_llm-router-contract.yml": "tests/repo_guards/test_router_contract_batches.py",
}


# Consumers of ai_orchestration_otel_endpoint; the OTEL test renders each one.
# The file is selected only when the checkout contains it.
OTEL_CONSUMER_TEST = "tests/hermes_agent/test_otel_endpoint_consumers.py"
OTEL_CONSUMER_ROLES = {
    "llm_router", "agent_exec", "dify_docker", "langgraph_docker", "hindsight_docker",
    "hermes_agent", "open_webui", "agentgateway_docker",
}

# Role closures use the existing contract directories and playbooks. Shared GPU
# roles consume the same profiles, model-store contract and rendered units.
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
    "dify_docker": set(),
    "langflow_docker": {"tests/agent_concurrency/"},
    "langfuse_docker": {"tests/langfuse_docker/"},
    "hindsight_docker": {"tests/hindsight_docker/"},
    "hindsight_bank_dr": {"tests/hindsight_bank_dr/"},
    "llama_cpp": {"tests/llama_cpp/"},
    "llamacpp_release": {"tests/llamacpp_release/", "tests/llamacpp_serving/"},
    "llamacpp_serving": GPU_PYTEST,
    "vllm_serving": GPU_PYTEST,
    "nvidia_gpu_guest": GPU_PYTEST,
    "llm_gpu_serving": GPU_PYTEST,
    "openbao_secrets": {"tests/test_openbao_login_classification.py",
                        "tests/test_openbao_secrets_approle_name_resolution.py"},
    "phoenix_docker": set(),
    "llamaindex": set(),
    "qdrant_docker": {"tests/qdrant_docker/"},
    "agent_guest": set(),
    "herdr_server": set(),
    "herdr_remote": set(),
    "nixos_deploy": set(),
}
TEST_SCOPES = {target.removeprefix("tests/").rstrip("/")
               for targets in ROLE_TESTS.values() for target in targets if target.endswith("/")}
TEST_SCOPES.update({"llm_router", "llm_model_campaign", "inventory_load", "repo_guards",
                    "dify_docker", "phoenix_docker", "openbao_secrets", "llamaindex", "agent_guest"})


class Selection(TypedDict):
    pytest_targets: list[str]
    ansible_tests: list[str]
    llm_router_playbooks: list[str]
    run_inventory: bool
    run_selector_checks: bool
    unknown: list[str]


def llm_router_matrix() -> list[list[str]]:
    workflow = Path(".github/workflows/_llm-router-contract.yml").read_text()
    in_matrix = False
    entries: list[list[str]] = []
    for line in workflow.splitlines():
        if re.match(r"\s*playbook:", line):
            in_matrix = True
            continue
        if in_matrix:
            match = re.match(r"\s*#?\s*-\s*(tests/llm_router/[^#]+)", line)
            if match:
                entries.append(match.group(1).split())
            elif entries and (continuation := re.match(r"\s*#\s+(tests/llm_router/[^#]+)", line)):
                entries[-1].extend(continuation.group(1).split())
            elif line.strip() and not line.lstrip().startswith("#") and not line.startswith(" "):
                break
    if not entries:
        raise ValueError("could not read explicit LLM Router matrix entries")
    return entries


def router_imports(matrix: list[list[str]]) -> dict[str, list[str]]:
    importers: dict[str, list[str]] = {}
    for entry in matrix:
        for playbook in entry:
            for target in re.findall(r"import_playbook:\s*(\S+)", Path(playbook).read_text()):
                imported = (Path(playbook).parent / target).as_posix()
                importers.setdefault(imported, []).append(" ".join(entry))
    return importers


def full_selection() -> Selection:
    return {
        "pytest_targets": ["tests/"],
        "ansible_tests": sorted(ANSIBLE_TESTS),
        "llm_router_playbooks": [" ".join(entry) for entry in llm_router_matrix()],
        "run_inventory": True,
        "run_selector_checks": True,
        "unknown": [],
    }


def select(paths: list[str]) -> Selection:
    pytest_targets: set[str] = set()
    ansible_tests: set[str] = set()
    router_tests: set[str] = set()
    run_inventory = False
    run_selector_checks = False
    route_full = False
    unknown: list[str] = []
    matrix = llm_router_matrix()
    matrix_by_test = {test: entry for entry in matrix for test in entry}

    def role_scope(role: str) -> None:
        pytest_targets.update(ROLE_TESTS.get(role, set()))
        if role in OTEL_CONSUMER_ROLES and Path(OTEL_CONSUMER_TEST).is_file():
            pytest_targets.add(OTEL_CONSUMER_TEST)
        ansible_tests.update(test for test in ANSIBLE_TESTS if test.startswith(f"tests/{role}/"))
        if role in {"llamacpp_serving", "vllm_serving", "nvidia_gpu_guest", "llm_gpu_serving"}:
            ansible_tests.update(GPU_ANSIBLE)
        if role == "llm_router":
            # hermes_agent tests read the router role defaults (alias and role
            # contract), so any router change selects them too.
            pytest_targets.update({"tests/llm_gpu_engine_roles/", "tests/hermes_agent/"})
            router_tests.update(" ".join(entry) for entry in matrix)

    for raw_path in paths:
        path = raw_path.removeprefix("./")
        if not path:
            continue
        if path in PYTEST_PATH_TARGETS:
            pytest_targets.add(PYTEST_PATH_TARGETS[path])
        if path.startswith("tests/"):
            parts = Path(path).parts
            owner = parts[1] if len(parts) > 2 else ""
            if path == "tests/fixtures/llm-router-target-output.yml":
                role_scope("llm_router")
                continue
            if path in {"tests/inventory_load/tofu_inventory.json", "tests/inventory_load/verify_inventory.yml",
                        "tests/inventory_load/test_ssh_probe_result_selection.yml"}:
                run_inventory = True
                continue
            removed = not Path(path).exists()
            if owner in TEST_SCOPES and (removed or "fixtures" in parts or "tasks" in parts
                                        or Path(path).name.startswith("_")):
                if owner == "llm_router":
                    role_scope(owner)
                elif owner == "inventory_load":
                    run_inventory = True
                elif owner == "repo_guards":
                    ansible_tests.add("tests/repo_guards/test_env_guards_actually_fire.yml")
                elif owner in ROLE_TESTS:
                    role_scope(owner)
                else:
                    if owner not in {"dify_docker", "phoenix_docker"}:
                        pytest_targets.add(f"tests/{owner}/")
                    ansible_tests.update(test for test in ANSIBLE_TESTS if test.startswith(f"tests/{owner}/"))
                continue
            if removed:
                # Nothing is left to run. The owner scope above, or the manifest
                # edit that dropped the file, selects what the removal affects.
                continue
            if path.startswith("tests/llm_router/") and path.endswith((".yml", ".yaml")):
                if path in matrix_by_test:
                    router_tests.add(" ".join(matrix_by_test[path]))
                elif importers := router_imports(matrix).get(path):
                    router_tests.update(importers)
                else:
                    unknown.append(raw_path)
            elif Path(path).suffix == ".py" and Path(path).is_file():
                pytest_targets.add(path)
            elif Path(path).suffix in {".yml", ".yaml"} and Path(path).is_file():
                if path in ANSIBLE_TESTS:
                    ansible_tests.add(path)
                else:
                    unknown.append(raw_path)
            elif Path(path).is_dir():
                pytest_targets.add(path)
            else:
                unknown.append(raw_path)
        elif path.startswith("roles/"):
            role = path.split("/")[1]
            if role in ROLE_TESTS or role == "llm_router":
                role_scope(role)
            else:
                route_full = True  # unmapped role: no contract family to narrow to
        elif path.startswith("llm-models.d/"):
            role_scope("llm_router")
            pytest_targets.update(GPU_PYTEST | {"tests/llm_model_campaign/", "tests/hermes_agent/"})
            ansible_tests.update(GPU_ANSIBLE)
            run_selector_checks = True
        elif path.startswith("molecule/") and Path(path).parts[1] in {
                directory.name for directory in Path("molecule").iterdir() if directory.is_dir()}:
            # The shared scenario selector owns this path's Molecule execution.
            run_selector_checks = True
        elif path == "scripts/generate_servable_aliases.py":
            role_scope("llm_router")
            run_selector_checks = True
        elif path == "scripts/run-ansible.sh":
            pytest_targets.update({"tests/test_run_ansible_runner.py", "tests/test_run_ansible_identity.py"})
        elif path == "scripts/verify-pinned-patches.py":
            role_scope("hermes_agent")
        elif path == "renovate.json":
            run_selector_checks = True
        elif path.startswith(("inventory/", "group_vars/", "host_vars/", "playbooks/")) or path == "requirements.yml":
            route_full = True  # every role and router playbook reads these files
        elif path == ".github/workflows/_llm-router-contract.yml":
            role_scope("llm_router")
            run_selector_checks = True
        elif path.startswith((".github/workflows/", ".github/scripts/")) or path == ".github/requirements-ci.txt":
            run_selector_checks = True
            pytest_targets.update({"tests/test_contract_scope_selector.py", "tests/test_contract_scope_mapping.py",
                                  "tests/test_ci_workflow_policy.py", "tests/test_ci_gate_dispatch.py"})
        elif (path.lower().endswith((".md", ".mdx", ".txt")) or path.startswith("docs/")
              or path == ".release-please-manifest.json"):
            continue
        else:
            route_full = True  # unmapped root file: no narrower scope is known

    unknown_paths = sorted(set(unknown))
    if route_full:
        return {**full_selection(), "unknown": unknown_paths}
    return {
        "pytest_targets": sorted(pytest_targets),
        "ansible_tests": sorted(ansible_tests),
        "llm_router_playbooks": [" ".join(entry) for entry in matrix
                                 if " ".join(entry) in router_tests],
        "run_inventory": run_inventory,
        "run_selector_checks": run_selector_checks,
        "unknown": unknown_paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--paths-file", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if args.full:
        result = full_selection()
    else:
        paths = args.paths
        if args.paths_file:
            paths = Path(args.paths_file).read_text().splitlines()
        result = select(paths)
    if result["unknown"]:
        print("Unmapped contract paths: " + ", ".join(result["unknown"]), file=sys.stderr)
        return 2
    encoded = json.dumps(result, sort_keys=True)
    if args.output:
        with open(args.output, "a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={json.dumps(value)}\n")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
