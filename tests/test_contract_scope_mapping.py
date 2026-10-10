from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SELECTOR = Path(__file__).parents[1] / ".github/scripts/select-contract-scope.py"
CI_GATE = Path(__file__).parents[1] / ".github/workflows/ci-gate.yml"


def run_selector(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SELECTOR), *paths],
        check=False,
        capture_output=True,
        text=True,
    )


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
    assert len(selection["llm_router_playbooks"]) == 99
    assert "tests/nvidia_gpu_guest/test_cache_only_sync.yml" in selection["ansible_tests"]


@pytest.mark.parametrize("path", [
    "inventory/group_vars/all.yml", "group_vars/all.yml", "host_vars/example.yml",
    "playbooks/site.yml", "requirements.yml",
])
def test_inventory_and_playbook_edits_route_to_full_suite(path: str) -> None:
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    full = json.loads(run_selector("--full").stdout)
    assert selection["pytest_targets"] == ["tests/"]
    assert set(selection["llm_router_playbooks"]) == set(full["llm_router_playbooks"])


@pytest.mark.parametrize("path", [
    "roles/fabric_watchdog/defaults/main.yml", "roles/langfuse_docker/tasks/main.yml",
    "roles/llamacpp_serving/tasks/install.yml", "roles/nvidia_gpu_guest/tasks/cache-sync.yml",
    "roles/vllm_serving/tasks/install.yml",
    "tests/langfuse_docker/fixtures/api-response-shapes.json",
    "tests/llm_gpu_engine_roles/fixtures/pro6000-target/nvidia-smi-query.csv",
    "tests/inventory_load/tofu_inventory.json",
])
def test_caller_contract_mapping_covers_registered_production_inputs(path: str) -> None:
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_contract_filters"]
    assert any(fnmatch.fnmatchcase(path, pattern) for pattern in yaml.safe_load(filters)["contract_only"])


def test_caller_contract_mapping_retains_unknown_role_rejection() -> None:
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
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_contract_filters"]
    assert any(fnmatch.fnmatchcase(path, pattern) for pattern in yaml.safe_load(filters)["contract_only"])
    result = run_selector(path)
    assert result.returncode == 0, result.stderr
    assert "tests/hermes_agent/" in json.loads(result.stdout)["pytest_targets"]


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
    assert len(paths) == len(set(paths)) == 99


def test_changed_router_groups_follow_manifest_order_not_path_order() -> None:
    full = json.loads(run_selector("--full").stdout)["llm_router_playbooks"]
    chosen = [full[0], full[-1]]
    result = run_selector(*(group.split()[0] for group in reversed(chosen)))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["llm_router_playbooks"] == chosen


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
    filters = yaml.safe_load(CI_GATE.read_text())["jobs"]["ci"]["with"]["molecule_contract_filters"]
    contract_only = yaml.safe_load(filters)["contract_only"]
    for path in ("roles/llamacpp_release/defaults/main/00-release.yml",
                 "roles/openbao_secrets/defaults/main/10-domains.yml"):
        assert not any(fnmatch.fnmatchcase(path, pattern) for pattern in contract_only)


@pytest.mark.parametrize(("path", "guard"), [
    (".github/scripts/check-installer-sha.sh", "tests/llamacpp_release/test_checksum_gate.py"),
    (".github/workflows/fix-installer-sha.yml", "tests/repo_guards/test_installer_sha_workflow_race.py"),
    (".github/workflows/_llm-router-contract.yml", "tests/repo_guards/test_router_contract_batches.py"),
])
def test_ci_contract_edits_select_their_guard_test(path: str, guard: str) -> None:
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    assert guard in json.loads(result.stdout)["pytest_targets"]


def _run_in(cwd: Path, *paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SELECTOR), *paths], check=False, capture_output=True, text=True, cwd=cwd,
    )


def _copy_router_manifest(root: Path) -> None:
    workflows = root / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "_llm-router-contract.yml").write_text((CI_GATE.parent / "_llm-router-contract.yml").read_text())


OTEL_TEST = "tests/hermes_agent/test_otel_endpoint_consumers.py"


@pytest.mark.parametrize("role", [
    "llm_router", "agent_exec", "dify_docker", "langgraph_docker", "hindsight_docker",
    "hermes_agent", "open_webui", "agentgateway_docker",
])
def test_otel_consumer_edits_select_the_otel_test(tmp_path: Path, role: str) -> None:
    (tmp_path / OTEL_TEST).parent.mkdir(parents=True)
    (tmp_path / OTEL_TEST).write_text("")
    _copy_router_manifest(tmp_path)
    result = _run_in(tmp_path, f"roles/{role}/tasks/main.yml")

    assert result.returncode == 0, result.stderr
    targets = json.loads(result.stdout)["pytest_targets"]
    assert OTEL_TEST in targets or "tests/" in targets


def test_otel_mapping_is_inert_while_the_test_file_is_absent(tmp_path: Path) -> None:
    _copy_router_manifest(tmp_path)
    result = _run_in(tmp_path, "roles/dify_docker/tasks/main.yml")

    assert result.returncode == 0, result.stderr
    assert OTEL_TEST not in json.loads(result.stdout)["pytest_targets"]


def test_ci_requirements_edit_selects_the_selector_checks() -> None:
    result = run_selector(".github/requirements-ci.txt")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["run_selector_checks"]
    assert "tests/test_contract_scope_selector.py" in selection["pytest_targets"]
