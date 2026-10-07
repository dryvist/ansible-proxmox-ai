#!/usr/bin/env python3
"""Select focused contract tests from an explicit changed-path mapping."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

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
    "tests/llamaindex/test_hardening.yml",
    "tests/llm_gpu_legacy/test_render_host_contract.yml",
    "tests/llm_gpu_legacy/test_render_host_contract_all.yml",
}


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
            elif line.strip() and not line.lstrip().startswith("#") and not line.startswith(" "):
                break
    if not entries:
        raise ValueError("could not read explicit LLM Router matrix entries")
    return entries


def select(paths: list[str]) -> dict[str, object]:
    pytest_targets: set[str] = set()
    ansible_tests: set[str] = set()
    router_tests: set[str] = set()
    run_inventory = False
    run_selector_checks = False
    unknown: list[str] = []
    matrix = llm_router_matrix()
    matrix_by_test = {test: entry for entry in matrix for test in entry}

    for raw_path in paths:
        path = raw_path.removeprefix("./")
        if not path:
            continue
        if path.startswith("tests/"):
            if path.startswith("tests/llm_router/") and path.endswith((".yml", ".yaml")):
                if path not in matrix_by_test:
                    unknown.append(raw_path)
                else:
                    router_tests.add(" ".join(matrix_by_test[path]))
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
        elif path.startswith("roles/hermes_agent/"):
            pytest_targets.add("tests/hermes_agent/")
        elif path.startswith("roles/llm_router/"):
            router_tests.update(" ".join(entry) for entry in matrix)
        elif path.startswith(("inventory/", "group_vars/", "host_vars/", "playbooks/")) or path == "requirements.yml":
            run_inventory = True
            run_selector_checks = True
        elif path.startswith(".github/workflows/") or path.startswith(".github/scripts/"):
            run_selector_checks = True
            pytest_targets.add("tests/test_contract_scope_selector.py")
        elif path.lower().endswith((".md", ".mdx", ".txt")) or path.startswith("docs/"):
            continue
        else:
            unknown.append(raw_path)

    return {
        "pytest_targets": sorted(pytest_targets),
        "ansible_tests": sorted(ansible_tests),
        "llm_router_playbooks": sorted(router_tests),
        "run_inventory": run_inventory,
        "run_selector_checks": run_selector_checks,
        "unknown": sorted(set(unknown)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--paths-file", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if args.full:
        result = {
            "pytest_targets": ["tests/"],
            "ansible_tests": [],
            "llm_router_playbooks": [" ".join(entry) for entry in llm_router_matrix()],
            "run_inventory": True,
            "run_selector_checks": True,
            "unknown": [],
        }
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
