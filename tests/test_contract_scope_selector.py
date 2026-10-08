from __future__ import annotations

import json
import os
import re

from jinja2 import Environment, StrictUndefined

import pytest
import yaml
import subprocess
import sys
from pathlib import Path


SELECTOR = Path(__file__).parents[1] / ".github/scripts/select-contract-scope.py"
CI_GATE = Path(__file__).parents[1] / ".github/workflows/ci-gate.yml"


def run_selector(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SELECTOR), *paths],
        check=False,
        capture_output=True,
        text=True,
    )


def test_hermes_agent_role_selects_its_role_tests() -> None:
    result = run_selector("roles/hermes_agent/defaults/main/10-installer-and-bundles.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == ["tests/hermes_agent/"]
    assert selection["llm_router_playbooks"] == []


def test_changed_llm_router_playbook_selects_its_matrix_entry() -> None:
    result = run_selector("tests/llm_router/test_review_key_scopes.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert len(selection["llm_router_playbooks"]) == 1
    assert "tests/llm_router/test_review_key_scopes.yml" in selection["llm_router_playbooks"][0].split()


def test_changed_router_playbook_selects_only_its_playbook() -> None:
    result = run_selector("tests/llm_router/test_service_restart_policy.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert len(selection["llm_router_playbooks"]) == 1
    assert selection["llm_router_playbooks"][0].split() == [
        "tests/llm_router/test_service_restart_policy.yml"
    ]


def test_unmapped_role_fails_fast() -> None:
    result = run_selector("roles/unmapped_role/tasks/main.yml")

    assert result.returncode == 2
    assert "Unmapped contract paths" in result.stderr


def test_llm_router_role_paths_select_the_complete_router_contract() -> None:
    result = run_selector("roles/llm_router/tasks/main.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    full = json.loads(run_selector("--full").stdout)
    assert set(selection["llm_router_playbooks"]) == set(full["llm_router_playbooks"])
    assert {"tests/llm_gpu_engine_roles/", "tests/hermes_agent/"} <= set(selection["pytest_targets"])


def test_unmapped_yaml_contract_fails_fast() -> None:
    result = run_selector("tests/phoenix_docker/unmapped-contract.yml")

    assert result.returncode == 2
    assert "Unmapped contract paths" in result.stderr


def test_changed_python_test_selects_only_that_file() -> None:
    result = run_selector("tests/test_contract_scope_selector.py")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == ["tests/test_contract_scope_selector.py"]


def test_full_suite_keeps_every_router_matrix_entry() -> None:
    result = subprocess.run(
        [sys.executable, str(SELECTOR), "--full"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert len(selection["llm_router_playbooks"]) == 94


def test_full_suite_covers_main_pushes_and_promotion_prs() -> None:
    workflow = CI_GATE.read_text()

    assert 'EVENT_NAME" == push && "$GITHUB_REF" == refs/heads/main' in workflow
    assert 'BASE_SHA="$PUSH_BEFORE"' in workflow
    assert 'HEAD_SHA="$PUSH_HEAD"' in workflow
    molecule = yaml.safe_load(workflow)["jobs"]["molecule"]
    assert "github.event.pull_request.base.ref == 'main'" in molecule["with"]["full_suite"]
    assert molecule["if"].count("github.event.pull_request.base.ref == 'main'") == 2
    assert "inputs.full_suite && steps.find.outputs.scenarios" in (
        CI_GATE.parents[0] / "_molecule.yml"
    ).read_text()
    assert "github.event_name == 'push' && github.ref == 'refs/heads/develop'" in workflow
    matrix_edit = json.loads(run_selector(".github/workflows/_llm-router-contract.yml").stdout)
    full = json.loads(run_selector("--full").stdout)
    assert matrix_edit["run_selector_checks"]
    assert set(matrix_edit["llm_router_playbooks"]) == set(full["llm_router_playbooks"])
    assert "tests/test_contract_scope_selector.py" in workflow


@pytest.mark.parametrize(("path", "target"), [
    ("roles/nvidia_gpu_guest/tasks/cache-sync.yml", "tests/nvidia_gpu_guest/"),
    ("roles/llm_gpu_serving/tasks/main.yml", "tests/llm_gpu_engine_roles/"),
    ("roles/llamacpp_serving/tasks/install.yml", "tests/llamacpp_serving/"),
    ("roles/vllm_serving/tasks/install.yml", "tests/vllm_serving/"),
    ("roles/llama_cpp/tasks/main.yml", "tests/llama_cpp/"),
    ("roles/llamacpp_release/tasks/load.yml", "tests/llamacpp_release/"),
    ("roles/langflow_docker/templates/docker-compose.yml.j2", "tests/agent_concurrency/"),
    ("roles/langfuse_docker/tasks/reconcile-stage0-evaluation.yml", "tests/langfuse_docker/"),
])
def test_production_role_selects_its_existing_contract_family(path: str, target: str) -> None:
    result = run_selector(path)
    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert target in selection["pytest_targets"]
    assert "tests/" not in selection["pytest_targets"]


@pytest.mark.parametrize("path", [
    "tests/langfuse_docker/fixtures/api-response-shapes.json",
    "tests/llm_gpu_engine_roles/fixtures/pro6000-target/nvidia-smi-query.csv",
    "tests/llm_router/fixtures/seed-key-response-shape.json",
    "tests/llm_router/tasks/parity_setup.yml",
    "tests/llm_model_campaign/fixtures/runner-missing-uv.yml",
    "tests/fixtures/llm-router-target-output.yml",
    "tests/inventory_load/tofu_inventory.json",
    "molecule/hindsight/verify.yml",
    "renovate.json",
])
def test_contract_inputs_select_owner_coverage(path: str) -> None:
    result = run_selector(path)
    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert any(selection[key] for key in (
        "pytest_targets", "ansible_tests", "llm_router_playbooks", "run_inventory", "run_selector_checks"))


def test_registry_selects_all_consumers_without_global_pytest() -> None:
    result = run_selector("llm-models.d/60-gpu.yml")
    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["run_selector_checks"]
    assert {"tests/nvidia_gpu_guest/", "tests/llm_model_campaign/", "tests/hermes_agent/"} <= set(
        selection["pytest_targets"])
    assert len(selection["llm_router_playbooks"]) == 94
    assert "tests/nvidia_gpu_guest/test_cache_only_sync.yml" in selection["ansible_tests"]


@pytest.mark.parametrize(("event", "ref", "base", "expected_full"), [
    ("pull_request", "refs/pull/1/merge", "main", True),
    ("pull_request", "refs/pull/1/merge", "develop", False),
    ("push", "refs/heads/main", "", True),
    ("push", "refs/heads/develop", "", False),
])
def test_actual_scope_step_dispatches_full_or_focused(
    tmp_path: Path, event: str, ref: str, base: str, expected_full: bool,
) -> None:
    step = next(step for step in yaml.safe_load(CI_GATE.read_text())["jobs"]["contract-scope"]["steps"]
                if step.get("id") == "select")
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    git = mock_bin / "git"
    git.write_text("#!/bin/sh\nprintf '%s\\n' tests/test_contract_scope_selector.py\n")
    git.chmod(0o755)
    output = tmp_path / "output"
    env = dict(os.environ, EVENT_NAME=event, GITHUB_REF=ref, BASE_REF=base,
               BASE_SHA="base", HEAD_SHA="head", PUSH_BEFORE="before", PUSH_HEAD="push",
               GITHUB_OUTPUT=str(output), RUNNER_TEMP=str(tmp_path),
               PATH=str(mock_bin) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    selected = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert selected["full_suite"] == str(expected_full).lower()
    targets = json.loads(selected["pytest_targets"])
    assert targets == (["tests/"] if expected_full else ["tests/test_contract_scope_selector.py"])
    assert len(json.loads(selected["llm_router_playbooks"])) == (94 if expected_full else 0)


@pytest.mark.parametrize(("event", "ref", "base", "allows_skips"), [
    ("pull_request", "refs/pull/1/merge", "main", False),
    ("pull_request", "refs/pull/1/merge", "develop", True),
    ("push", "refs/heads/main", "", False),
    ("push", "refs/heads/develop", "", True),
])
def test_actual_gate_policy_requires_full_main_results(
    event: str, ref: str, base: str, allows_skips: bool,
) -> None:
    gate = yaml.safe_load(CI_GATE.read_text())["jobs"]["gate"]
    step = next(step for step in gate["steps"] if step.get("name") == "Check all results")
    clause = step["env"]["CI_GATE_ALLOWED_SKIPS"].strip().removeprefix("${{").removesuffix("}}")
    clause = re.sub(r"!(?!=)", "not ", clause.replace("&&", "and").replace("||", "or"))
    allowed = Environment(undefined=StrictUndefined).compile_expression(clause)(github={
        "event_name": event, "ref": ref, "event": {"pull_request": {"base": {"ref": base}}},
    })
    allowed_set = {name.strip() for name in allowed.split(",") if name.strip()}
    required = {"data-contract", "molecule"}
    assert required <= set(gate["needs"])
    assert (required <= allowed_set) is allows_skips
    assert bool(required - allowed_set) is (not allows_skips)


@pytest.mark.parametrize("path", [
    "roles/fabric_watchdog/defaults/main.yml", "roles/langfuse_docker/tasks/main.yml",
    "roles/llamacpp_serving/tasks/install.yml", "roles/nvidia_gpu_guest/tasks/cache-sync.yml",
    "roles/openbao_secrets/defaults/main/10-domains.yml", "roles/vllm_serving/tasks/install.yml",
    "tests/langfuse_docker/fixtures/api-response-shapes.json",
    "tests/llm_gpu_engine_roles/fixtures/pro6000-target/nvidia-smi-query.csv",
    "tests/inventory_load/tofu_inventory.json",
])
def test_caller_contract_mapping_covers_registered_production_inputs(path: str) -> None:
    import fnmatch
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_contract_filters"]
    assert any(fnmatch.fnmatchcase(path, pattern) for pattern in yaml.safe_load(filters)["contract_only"])


def test_caller_contract_mapping_retains_unknown_role_rejection() -> None:
    import fnmatch
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_contract_filters"]
    assert not any(fnmatch.fnmatchcase("roles/unmapped_role/tasks/main.yml", pattern)
                   for pattern in yaml.safe_load(filters)["contract_only"])


@pytest.mark.parametrize("path", [
    "roles/dify_docker/templates/docker-compose.yml.j2",
    "roles/langflow_docker/defaults/main.yml",
    "roles/langflow_docker/templates/docker-compose.yml.j2",
])
def test_compose_path_adds_python_contract_and_preserves_role_scope(path: str) -> None:
    result = run_selector(path)
    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert "tests/agent_concurrency/test_runner_compose_caps.py" in selection["pytest_targets"]
    if path.startswith("roles/dify_docker/"):
        assert {"tests/dify_docker/test_db_password.yml",
                "tests/dify_docker/test_admin_password_reset.yml"} <= set(selection["ansible_tests"])
    else:
        assert "tests/agent_concurrency/" in selection["pytest_targets"]


def test_seed_fixture_adds_python_guard_and_preserves_full_router_scope() -> None:
    result = run_selector("tests/llm_router/fixtures/seed-key-response-shape.json")
    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert "tests/llm_router/test_seed_key_sensitivity_guard.py" in selection["pytest_targets"]
    assert "tests/llm_gpu_engine_roles/" in selection["pytest_targets"]
    full = subprocess.run([sys.executable, str(SELECTOR), "--full"],
                          check=False, capture_output=True, text=True)
    assert full.returncode == 0, full.stderr
    assert set(selection["llm_router_playbooks"]) == set(json.loads(full.stdout)["llm_router_playbooks"])


@pytest.mark.parametrize("path", [
    "roles/hermes_agent/defaults/main/50-webhook-persona-api.yml",
    "roles/hermes_agent/defaults/main/60-kanban-dispatcher.yml",
])
def test_captured_hermes_inputs_reach_existing_contract_family(path: str) -> None:
    import fnmatch
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_contract_filters"]
    assert any(fnmatch.fnmatchcase(path, pattern) for pattern in yaml.safe_load(filters)["contract_only"])
    result = run_selector(path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pytest_targets"] == ["tests/hermes_agent/"]


@pytest.mark.parametrize("path", [
    "roles/llm_router/tasks/main.yml",
    "llm-models.d/10-large.yml",
    "tests/fixtures/llm-router-target-output.yml",
])
def test_router_scope_preserves_manifest_execution_order(path: str) -> None:
    result = run_selector(path)
    assert result.returncode == 0, result.stderr
    full = json.loads(run_selector("--full").stdout)["llm_router_playbooks"]
    selected = json.loads(result.stdout)["llm_router_playbooks"]
    assert selected == full
    paths = [playbook for group in selected for playbook in group.split()]
    assert len(paths) == len(set(paths)) == 96


def test_changed_router_groups_follow_manifest_order_not_path_order() -> None:
    full = json.loads(run_selector("--full").stdout)["llm_router_playbooks"]
    chosen = [full[0], full[-1]]
    result = run_selector(*(group.split()[0] for group in reversed(chosen)))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["llm_router_playbooks"] == chosen
