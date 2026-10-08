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


def test_changed_paired_router_playbook_keeps_its_pair() -> None:
    result = run_selector("tests/llm_router/test_service_restart_policy.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert len(selection["llm_router_playbooks"]) == 1
    assert {"tests/llm_router/test_service_restart_policy.yml",
            "tests/llm_router/test_syslog_route_outside_rolling_play.yml"} <= set(
        selection["llm_router_playbooks"][0].split())


def test_unmapped_role_fails_fast() -> None:
    result = run_selector("roles/unmapped_role/tasks/main.yml")

    assert result.returncode == 2
    assert "Unmapped contract paths" in result.stderr


def test_llm_router_role_paths_fail_fast_until_a_focused_mapping_exists() -> None:
    result = run_selector("roles/llm_router/tasks/main.yml")

    assert result.returncode == 2
    assert "Unmapped contract paths" in result.stderr


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
    assert len(selection["llm_router_playbooks"]) == 12


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
