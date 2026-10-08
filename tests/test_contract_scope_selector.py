from __future__ import annotations

import json
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


def selected_router_tests(*paths: str) -> set[str]:
    result = run_selector(*paths)

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    return {
        test
        for group in selection["llm_router_playbooks"]
        for test in group.split()
    }


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
    assert selection["llm_router_playbooks"] == ["tests/llm_router/test_review_key_scopes.yml"]


def test_changed_paired_router_playbook_keeps_its_pair() -> None:
    result = run_selector("tests/llm_router/test_service_restart_policy.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["llm_router_playbooks"] == [
        "tests/llm_router/test_service_restart_policy.yml tests/llm_router/test_syslog_route_outside_rolling_play.yml"
    ]


def test_unmapped_role_fails_fast() -> None:
    result = run_selector("roles/unmapped_role/tasks/main.yml")

    assert result.returncode == 2
    assert "Unmapped contract paths" in result.stderr


def test_unmapped_llm_router_role_paths_fail_fast() -> None:
    result = run_selector("roles/llm_router/tasks/main.yml")

    assert result.returncode == 2
    assert "Unmapped contract paths" in result.stderr


def test_model_registry_data_selects_registry_render_parity() -> None:
    selected = selected_router_tests("llm-models.d/10-large.yml")

    assert "tests/llm_router/test_registry_render_parity.yml" in selected


def test_changed_llm_router_role_paths_select_focused_contracts() -> None:
    paths = (
        "roles/llm_router/defaults/main/40-routing.yml",
        "roles/llm_router/defaults/main/56-static-virtual-keys.yml",
        "roles/llm_router/defaults/main/59-key-scopes.yml",
        "roles/llm_router/tasks/drift-check.yml",
        "roles/llm_router/tasks/prepare-role-tag-updates.yml",
        "roles/llm_router/tasks/reconcile-seeded-key-policy.yml",
        "roles/llm_router/tasks/seed-keys.yml",
        "roles/llm_router/tasks/seed-roles.yml",
        "roles/llm_router/templates/config.yaml.j2",
        "roles/llm_router/templates/model-list-hermes-agents.yaml.j2",
        "roles/llm_router/templates/model-list-tags.yaml.j2",
        "roles/llm_router/templates/model-list.yaml.j2",
    )
    selected = selected_router_tests(*paths)

    assert {
        "tests/llm_router/test_auto_router_deployment_tags.yml",
        "tests/llm_router/test_deployment_class_tags.yml",
        "tests/llm_router/test_drift_live_key_shape.yml",
        "tests/llm_router/test_gpu_pro6000_enabled_render.yml",
        "tests/llm_router/test_hermes_agents.yml",
        "tests/llm_router/test_registry_render_parity.yml",
        "tests/llm_router/test_review_key_scopes.yml",
        "tests/llm_router/test_review_roles.yml",
        "tests/llm_router/test_router_ui_drift.yml",
        "tests/llm_router/test_seed_key_format_assert.yml",
        "tests/llm_router/test_seed_key_model_allowlist_reconcile.yml",
        "tests/llm_router/test_seed_key_route_model_reconcile.yml",
        "tests/llm_router/test_seed_key_trace_reconcile.yml",
        "tests/llm_router/test_zdr_key_tags.yml",
    } <= selected


def test_compose_roles_and_seed_fixture_select_their_python_contracts() -> None:
    result = run_selector(
        "roles/dify_docker/templates/docker-compose.yml.j2",
        "roles/langflow_docker/defaults/main.yml",
        "roles/langflow_docker/templates/docker-compose.yml.j2",
        "tests/llm_router/fixtures/seed-key-response-shape.json",
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == [
        "tests/agent_concurrency/test_runner_compose_caps.py",
        "tests/llm_router/test_seed_key_sensitivity_guard.py",
    ]


def test_molecule_contract_filters_cover_changed_role_and_test_paths() -> None:
    workflow = CI_GATE.read_text()

    for path in (
        "roles/dify_docker/templates/docker-compose.yml.j2",
        "roles/hermes_agent/**",
        "roles/langflow_docker/defaults/main.yml",
        "roles/langflow_docker/templates/docker-compose.yml.j2",
        "tests/agent_concurrency/**",
    ):
        assert f"- '{path}'" in workflow


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
    assert len(selection["llm_router_playbooks"]) == 92


def test_full_suite_is_limited_to_main_pushes() -> None:
    workflow = CI_GATE.read_text()

    assert 'EVENT_NAME" == push && "$GITHUB_REF" == refs/heads/main' in workflow
    assert 'BASE_SHA="$PUSH_BEFORE"' in workflow
    assert 'HEAD_SHA="$PUSH_HEAD"' in workflow
    assert "full_suite: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}" in workflow
    assert "inputs.full_suite && steps.find.outputs.scenarios" in (
        CI_GATE.parents[0] / "_molecule.yml"
    ).read_text()
    assert "github.event_name == 'push' && github.ref == 'refs/heads/develop'" in workflow
    assert "tests/test_contract_scope_selector.py" in workflow
