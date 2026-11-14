from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_contract_scope_mapping import CI_GATE, SELECTOR, run_selector


def test_hermes_agent_role_selects_its_role_tests() -> None:
    result = run_selector("roles/hermes_agent/defaults/main/10-installer-and-bundles.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert "tests/hermes_agent/" in selection["pytest_targets"]
    assert selection["llm_router_playbooks"] == []


def test_hermes_agent_group_vars_select_only_the_tests_that_read_them() -> None:
    result = run_selector("inventory/group_vars/hermes_agent_group/00-agent-settings.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert "tests/" not in selection["pytest_targets"]
    assert {"tests/hermes_agent/", "tests/openbao_secrets/test_roster_domains.py"} <= set(selection["pytest_targets"])
    assert selection["ansible_tests"] == ["tests/openbao_secrets/test_hermes_domain_view.yml"]
    assert selection["llm_router_playbooks"] == [{"name": "hermes", "playbooks": "tests/llm_router/test_hermes_agents.yml"}]
    assert selection["run_inventory"]
    assert not selection["run_selector_checks"]


def test_hermes_agent_change_set_selects_only_the_router_reader() -> None:
    result = run_selector(
        "inventory/group_vars/hermes_agent_group/00-agent-settings.yml",
        "roles/hermes_agent/defaults/main/100-splunk.yml",
        "roles/hermes_agent/templates/config-profile.yaml.j2",
        "roles/hermes_agent/templates/config.yaml.j2",
        "roles/hermes_agent/templates/hermes-env-profile.j2",
        "roles/hermes_agent/templates/hermes-env.j2",
        "tests/hermes_agent/test_owned_mcp_servers.py",
        "tests/hermes_agent/test_profile_contract.py",
        "tests/openbao_secrets/test_hermes_domain_view.yml",
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert "tests/" not in selection["pytest_targets"]
    assert "tests/hermes_agent/" in selection["pytest_targets"]
    assert "tests/openbao_secrets/test_roster_domains.py" in selection["pytest_targets"]
    assert selection["ansible_tests"] == ["tests/openbao_secrets/test_hermes_domain_view.yml"]
    assert selection["llm_router_playbooks"] == [{"name": "hermes", "playbooks": "tests/llm_router/test_hermes_agents.yml"}]
    assert selection["run_inventory"]


@pytest.mark.parametrize("path", [
    "roles/unmapped_role/tasks/main.yml", "roles/ollama/tasks/main.yml", "pyproject.toml",
])
def test_unmapped_paths_route_to_full_suite(path: str) -> None:
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pytest_targets"] == ["tests/"]
    assert f"::warning::unmapped path {path} selects the full suite" in result.stderr


def test_release_back_merge_paths_need_no_contracts() -> None:
    result = run_selector(".release-please-manifest.json", "CHANGELOG.md")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == []
    assert selection["llm_router_playbooks"] == []


def test_clickhouse_role_selects_contracts_and_retired_test_stays_retired() -> None:
    result = run_selector(
        "roles/clickhouse_docker/tasks/main.yml",
        "tests/langfuse_docker/test_code_eval_dispatcher.yml",
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == [
        "tests/clickhouse_docker/", "tests/langfuse_docker/test_clickhouse_contract.py",
    ]
    assert selection["ansible_tests"] == []


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


def test_unmapped_yaml_contract_routes_to_full_suite(tmp_path: Path) -> None:
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

    assert result.returncode == 0, result.stderr
    assert "Unmapped" not in result.stderr
    assert json.loads(result.stdout) == json.loads(run_selector("--full").stdout)


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


def test_campaign_only_change_set_selects_campaign_tests_without_router_matrix() -> None:
    result = run_selector(
        "playbooks/tasks/llm-model-campaign-paired-passes.yml",
        "tests/llm_model_campaign/test_paired_campaign.py",
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["pytest_targets"] == ["tests/llm_model_campaign/"]
    assert selection["llm_router_playbooks"] == []
    assert selection["ansible_tests"] == []
    assert not selection["run_inventory"]
    assert not selection["run_selector_checks"]


def test_campaign_file_beside_a_router_role_change_keeps_the_router_matrix() -> None:
    result = run_selector(
        "playbooks/llm-model-campaign.yml",
        "roles/llm_router/tasks/main.yml",
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    full = json.loads(run_selector("--full").stdout)
    assert selection["llm_router_playbooks"] == full["llm_router_playbooks"]
    assert "tests/llm_model_campaign/" in selection["pytest_targets"]


def test_router_read_campaign_file_keeps_the_full_suite() -> None:
    result = run_selector("playbooks/tasks/llm-model-campaign-endpoint-policy.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    full = json.loads(run_selector("--full").stdout)
    assert selection == full
