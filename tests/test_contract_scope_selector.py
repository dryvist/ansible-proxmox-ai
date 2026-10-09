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
WORKFLOWS = Path(__file__).parents[1] / ".github/workflows"


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


@pytest.mark.parametrize("path", [
    "roles/unmapped_role/tasks/main.yml", "roles/ollama/tasks/main.yml", "pyproject.toml",
])
def test_unmapped_paths_route_to_full_suite(path: str) -> None:
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pytest_targets"] == ["tests/"]


def test_llm_router_role_paths_select_the_complete_router_contract() -> None:
    result = run_selector("roles/llm_router/tasks/main.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    full = json.loads(run_selector("--full").stdout)
    assert set(selection["llm_router_playbooks"]) == set(full["llm_router_playbooks"])
    assert {"tests/llm_gpu_engine_roles/", "tests/hermes_agent/"} <= set(selection["pytest_targets"])


def test_unmapped_yaml_contract_fails_fast(tmp_path: Path) -> None:
    contract = tmp_path / "tests/phoenix_docker/unmapped-contract.yml"
    contract.parent.mkdir(parents=True)
    contract.write_text("---\n[]\n")
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "_llm-router-contract.yml").write_text(
        (CI_GATE.parent / "_llm-router-contract.yml").read_text())
    result = subprocess.run(
        [sys.executable, str(SELECTOR), "tests/phoenix_docker/unmapped-contract.yml"],
        check=False, capture_output=True, text=True, cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "Unmapped contract paths" in result.stderr


def test_removed_test_file_selects_its_owner_scope() -> None:
    result = run_selector("tests/hermes_agent/test_removed_contract.py")

    assert result.returncode == 0, result.stderr
    assert "tests/hermes_agent/" in json.loads(result.stdout)["pytest_targets"]


def test_removed_unowned_test_file_is_accepted() -> None:
    result = run_selector("tests/test_removed_contract.py")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pytest_targets"] == []


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
    "roles/vllm_serving/tasks/install.yml",
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


def test_agent_ci_fix_runs_only_for_pull_request_ci_gate_runs() -> None:
    job = yaml.safe_load((WORKFLOWS / "agent-ci-fix.yml").read_text())["jobs"]["ci-fix"]
    assert "vars.AI_AGENT_CI_FIX_ENABLED == 'true'" in job["if"]
    assert "github.event.workflow_run.event == 'pull_request'" in job["if"]


@pytest.mark.parametrize("workflow", ["agent-ci-fix.yml", "agent-pr-review-responder.yml"])
def test_agent_workflows_pin_the_shared_callee_to_a_commit(workflow: str) -> None:
    jobs = yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"].values()
    uses = [job["uses"] for job in jobs if "uses" in job]
    assert uses
    assert all(re.fullmatch(r"dryvist/ai-workflows/\.github/workflows/[\w.-]+@[0-9a-f]{40}", ref)
               for ref in uses), uses


def test_installer_recompute_authenticates_checksum_requests() -> None:
    steps = yaml.safe_load((WORKFLOWS / "fix-installer-sha.yml").read_text())["jobs"]["fix"]["steps"]
    recompute = next(step for step in steps if step.get("name") == "Recompute the checksum")
    assert recompute["env"]["GITHUB_TOKEN"] == "${{ github.token }}"


def test_nix_installs_whenever_the_agent_guest_test_runs() -> None:
    steps = yaml.safe_load((WORKFLOWS / "_data-contract.yml").read_text())["jobs"]["syntax-check"]["steps"]
    nix = next(step for step in steps if step.get("name") == "Install Nix")
    agent_guest = next(step for step in steps
                       if step.get("name") == "Verify agent_guest residual deny contract")
    assert nix["if"] == agent_guest["if"]


def test_report_step_reads_no_unused_selection_env() -> None:
    steps = yaml.safe_load(CI_GATE.read_text())["jobs"]["contract-scope"]["steps"]
    report = next(step for step in steps if step.get("name") == "Report the selected contract scope")
    assert "SELECTED" not in report["env"]


def test_ci_gate_header_names_the_contract_scope_gate() -> None:
    lines = CI_GATE.read_text().splitlines()
    header = "\n".join(lines[: lines.index("name: CI Gate")])
    assert "contract-scope" in header
    assert "All local jobs" not in header


@pytest.mark.parametrize(("scenario", "role"), [
    ("llama_cpp_backend", "llamacpp_release"),
    ("llm_gpu_serving", "llamacpp_release"),
    ("llm_gpu_serving", "nvidia_gpu_guest"),
    ("hindsight", "openbao_secrets"),
    ("hermes_ui", "openbao_secrets"),
])
def test_molecule_filters_cover_every_role_the_scenario_loads(scenario: str, role: str) -> None:
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_scenario_filters"]
    assert f"roles/{role}/**" in yaml.safe_load(filters)[scenario]


def test_roles_consumed_by_molecule_scenarios_are_not_contract_only() -> None:
    import fnmatch
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_contract_filters"]
    contract_only = yaml.safe_load(filters)["contract_only"]
    for path in ("roles/llamacpp_release/defaults/main/00-release.yml",
                 "roles/openbao_secrets/defaults/main/10-domains.yml"):
        assert not any(fnmatch.fnmatchcase(path, pattern) for pattern in contract_only)
