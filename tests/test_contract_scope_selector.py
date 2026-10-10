from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert "tests/hermes_agent/" in selection["pytest_targets"]
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


@pytest.mark.parametrize("playbook", [
    "tests/llm_router/test_drift_live_keys.yml",
    "tests/llm_router/test_hermes_public_key_alias_brain.yml",
])
def test_registered_router_playbook_selects_its_own_entry(playbook: str) -> None:
    result = run_selector(playbook)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["llm_router_playbooks"] == [playbook]


@pytest.mark.parametrize(("imported", "importer"), [
    ("tests/llm_router/test_ladder_keys_order.yml", "tests/llm_router/test_ladder_keys.yml"),
    ("tests/llm_router/test_ladder_keys_consumers.yml", "tests/llm_router/test_ladder_keys.yml"),
    ("tests/llm_router/test_ladder_keys_zdr.yml", "tests/llm_router/test_ladder_keys.yml"),
    ("tests/llm_router/test_gpu_pro6000_inactive_render.yml",
     "tests/llm_router/test_gpu_pro6000_render.yml"),
])
def test_imported_router_playbook_selects_its_importing_entry(imported: str, importer: str) -> None:
    result = run_selector(imported)

    assert result.returncode == 0, result.stderr
    entries = json.loads(result.stdout)["llm_router_playbooks"]
    assert len(entries) == 1
    assert importer in entries[0].split()


@pytest.mark.parametrize("path", [
    "roles/unmapped_role/tasks/main.yml", "roles/ollama/tasks/main.yml", "pyproject.toml",
])
def test_unmapped_paths_route_to_full_suite(path: str) -> None:
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pytest_targets"] == ["tests/"]


def test_release_back_merge_paths_need_no_contracts() -> None:
    result = run_selector(".release-please-manifest.json", "CHANGELOG.md")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == []
    assert selection["llm_router_playbooks"] == []


def test_nix_ai_pin_roles_are_mapped_without_contracts() -> None:
    result = run_selector(
        "roles/herdr_server/defaults/main.yml",
        "roles/herdr_remote/defaults/main.yml",
        "roles/nixos_deploy/defaults/main.yml",
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == []
    assert selection["ansible_tests"] == []


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
    assert len(selection["llm_router_playbooks"]) == 99
