#!/usr/bin/env python3
"""Select focused contract tests from changed paths."""

from __future__ import annotations

import argparse
import json
import re
import sys
from os import environ
from pathlib import Path
from typing import TypedDict

# runpy needs sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__" and sys.argv[1:] == ["--report"]:
    event, ref, base, result = (environ.get(key, "") for key in (
        "EVENT_NAME", "GITHUB_REF", "BASE_REF", "SELECT_RESULT"))
    full = (event == "push" and ref in {"refs/heads/main", "refs/heads/develop"}
            or event == "pull_request" and base == "main")
    scope = "full" if full else "failed-unmapped" if result != "success" else "focused"
    with Path(environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
        summary.write(f"Data Contract selection: {scope}\n")
    raise SystemExit(0)

from contract_scope_data import (
    ANSIBLE_TESTS,
    CAMPAIGN_PATHS,
    CAMPAIGN_TEST_SCOPE,
    GPU_ANSIBLE,
    GPU_PYTEST,
    OTEL_CONSUMER_ROLES,
    OTEL_CONSUMER_TEST,
    PYTEST_PATH_TARGETS,
    RETIRED_TESTS,
    ROLE_TESTS,
    TEST_SCOPES,
)
from router_shards import (
    RouterShard,
    load_router_contracts,
    router_default_changed_keys,
    router_default_playbooks,
    router_shard_target,
    router_shards,
)


class Selection(TypedDict):
    pytest_targets: list[str]
    ansible_tests: list[str]
    llm_router_playbooks: list[RouterShard]
    run_inventory: bool
    run_selector_checks: bool


def llm_router_matrix() -> list[list[str]]:
    return load_router_contracts()


def router_imports(matrix: list[list[str]]) -> dict[str, list[str]]:
    importers: dict[str, list[str]] = {}
    for entry in matrix:
        for playbook in entry:
            for target in re.findall(r"import_playbook:\s*(\S+)", Path(playbook).read_text()):
                imported = (Path(playbook).parent / target).as_posix()
                importers.setdefault(imported, []).append(" ".join(entry))
    return importers


def campaign_path(path: str) -> bool:
    return path.startswith(CAMPAIGN_PATHS) or path.startswith(CAMPAIGN_TEST_SCOPE)


def router_contract_reads(path: str) -> bool:
    name = Path(path).name
    return any(name in reader.read_text(errors="ignore")
               for reader in Path("tests/llm_router").rglob("*") if reader.is_file())


def warn_unmapped(path: str) -> None:
    print(f"::warning::unmapped path {path} selects the full suite", file=sys.stderr)


def full_selection() -> Selection:
    return {
        "pytest_targets": ["tests/"],
        "ansible_tests": sorted(ANSIBLE_TESTS),
        "llm_router_playbooks": router_shards(llm_router_matrix()),
        "run_inventory": True,
        "run_selector_checks": True,
    }


def select(paths: list[str], base_ref: str = "", head_ref: str = "") -> Selection:
    pytest_targets: set[str] = set()
    ansible_tests: set[str] = set()
    router_tests: set[str] = set()
    run_inventory = False
    run_selector_checks = False
    route_full = False
    matrix = llm_router_matrix()
    matrix_by_test = {test: entry for entry in matrix for test in entry}

    def role_scope(role: str, include_router_contracts: bool = True) -> None:
        pytest_targets.update(ROLE_TESTS.get(role, set()))
        if role in OTEL_CONSUMER_ROLES and Path(OTEL_CONSUMER_TEST).is_file():
            pytest_targets.add(OTEL_CONSUMER_TEST)
        ansible_tests.update(test for test in ANSIBLE_TESTS if test.startswith(f"tests/{role}/"))
        if role in {"dify_docker", "langflow_docker", "phoenix_docker"}:
            ansible_tests.add("tests/openbao_secrets/verify_hermes_api_key_seed.yml")
        if role in {"llamacpp_serving", "vllm_serving", "strata_serving", "nvidia_gpu_guest", "llm_gpu_serving"}:
            ansible_tests.update(GPU_ANSIBLE)
        if role == "llm_router":
            # hermes_agent tests read the router role defaults (alias and role
            # contract), so any router change selects them too.
            pytest_targets.update({"tests/llm_gpu_engine_roles/", "tests/hermes_agent/"})
            if include_router_contracts:
                router_tests.update(" ".join(entry) for entry in matrix)

    for raw_path in paths:
        path = raw_path.removeprefix("./")
        if not path:
            continue
        if path in PYTEST_PATH_TARGETS:
            pytest_targets.add(PYTEST_PATH_TARGETS[path])
        if path in RETIRED_TESTS:
            continue
        if campaign_path(path):
            # Router contract playbooks import some campaign task files, so a
            # read campaign file keeps the full suite.
            if router_contract_reads(path):
                route_full = True
            else:
                pytest_targets.add(CAMPAIGN_TEST_SCOPE)
            continue
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
                    warn_unmapped(path)
                    route_full = True
            elif path.startswith("tests/llm_router/") and path.endswith(".py"):
                role_scope("llm_router")
            elif Path(path).suffix == ".py" and Path(path).is_file():
                pytest_targets.add(path)
            elif Path(path).suffix in {".yml", ".yaml"} and Path(path).is_file():
                if path in ANSIBLE_TESTS:
                    ansible_tests.add(path)
                else:
                    warn_unmapped(path)
                    route_full = True
            elif Path(path).is_dir():
                pytest_targets.add(path)
            else:
                warn_unmapped(path)
                route_full = True
        elif path.startswith("roles/"):
            role = path.split("/")[1]
            if role in ROLE_TESTS or role == "llm_router":
                if role == "llm_router" and path.startswith("roles/llm_router/defaults/main/"):
                    role_scope(role, include_router_contracts=False)
                    changed_keys = router_default_changed_keys(path, base_ref, head_ref)
                    if changed_keys is None:
                        route_full = True
                    else:
                        entries, unresolved = router_default_playbooks(path, changed_keys, matrix)
                        if unresolved or not entries:
                            route_full = True
                        else:
                            router_tests.update(" ".join(entry) for entry in entries)
                else:
                    role_scope(role)
            else:
                warn_unmapped(path)
                route_full = True
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
            pytest_targets.add("tests/test_run_ansible_runner.py")
        elif path == "scripts/verify-pinned-patches.py":
            role_scope("hermes_agent")
        elif path == "renovate.json":
            run_selector_checks = True
        elif path.startswith("inventory/group_vars/hermes_agent_group/"):
            # Hermes-only vars: the role, the inventory load, and every test that loads the file.
            role_scope("hermes_agent")
            run_inventory = True
            for reader in sorted(test.as_posix() for test in Path("tests").rglob("*")
                                 if test.suffix in {".py", ".yml", ".yaml"}
                                 and not test.name.startswith("test_contract_scope")
                                 and Path(path).name in test.read_text(errors="ignore")):
                if reader in matrix_by_test:
                    router_tests.add(" ".join(matrix_by_test[reader]))
                elif reader.endswith(".py"):
                    pytest_targets.add(reader)
                elif reader in ANSIBLE_TESTS:
                    ansible_tests.add(reader)
                else:
                    warn_unmapped(reader)
                    route_full = True
        elif path.startswith(("inventory/", "group_vars/", "host_vars/", "playbooks/")) or path == "requirements.yml":
            route_full = True
        elif path == ".github/workflows/_llm-router-contract.yml":
            role_scope("llm_router")
            run_selector_checks = True
        elif path == ".github/router-contracts.yml":
            # Registry edits affect the workflow matrix, so run the router
            # workflow's registry validator against every current entry.
            router_tests.update(" ".join(entry) for entry in matrix)
            pytest_targets.update({
                "tests/test_contract_scope_selector.py",
                "tests/test_contract_scope_router_selector.py",
                "tests/test_router_shard_weights.py",
                "tests/repo_guards/test_router_contract_batches.py",
            })
        elif path.startswith((".github/workflows/", ".github/scripts/")) or path == ".github/requirements-ci.txt":
            run_selector_checks = True
            pytest_targets.update({"tests/test_contract_scope_selector.py", "tests/test_contract_scope_router_selector.py",
                                  "tests/test_contract_scope_mapping.py",
                                  "tests/test_ci_workflow_policy.py", "tests/test_ci_gate_dispatch.py",
                                  "tests/test_router_shard_weights.py",
                                  "tests/repo_guards/test_router_contract_batches.py"})
        elif (path.lower().endswith((".md", ".mdx", ".txt")) or path.startswith("docs/")
              or path == ".release-please-manifest.json"):
            continue
        else:
            warn_unmapped(path)
            route_full = True

    if route_full:
        return full_selection()
    return {
        "pytest_targets": sorted(pytest_targets),
        "ansible_tests": sorted(ansible_tests),
        "llm_router_playbooks": router_shards(
            [entry for entry in matrix if " ".join(entry) in router_tests], router_shard_target(matrix)),
        "run_inventory": run_inventory,
        "run_selector_checks": run_selector_checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--paths-file", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--skip-router", action="store_true",
                        help="omit router shards from a full selection for docs-only promotions")
    parser.add_argument("--base-ref", default="", help="base commit for changed defaults comparison")
    parser.add_argument("--head-ref", default="", help="head commit for changed defaults comparison")
    args = parser.parse_args()
    if args.full:
        result = full_selection()
    else:
        paths = args.paths
        if args.paths_file:
            paths = Path(args.paths_file).read_text().splitlines()
        result = select(paths, args.base_ref, args.head_ref)
    if args.skip_router:
        result["llm_router_playbooks"] = []
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
