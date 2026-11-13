from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from test_contract_scope_mapping import GENERATED_MATRIX, SELECTOR, run_selector, router_paths
from test_router_shard_weights import (
    SELECTOR_MODULE,
    router_shard_target,
    router_shards,
    router_shards_module,
    router_weight,
)


def test_changed_llm_router_playbook_selects_its_matrix_entry() -> None:
    result = run_selector("tests/llm_router/test_review_key_scopes.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert len(selection["llm_router_playbooks"]) == 1
    assert "tests/llm_router/test_review_key_scopes.yml" in selection["llm_router_playbooks"][0]["playbooks"].split()


def test_changed_router_playbook_selects_only_its_playbook() -> None:
    result = run_selector("tests/llm_router/test_service_restart_policy.yml")

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert len(selection["llm_router_playbooks"]) == 1
    assert selection["llm_router_playbooks"][0]["playbooks"].split() == [
        "tests/llm_router/test_service_restart_policy.yml"
    ]


def test_new_router_test_and_registry_entry_select_only_that_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    new_test = "tests/llm_router/test_new_registry_entry.yml"
    config = yaml.safe_load(router_shards_module.ROUTER_CONTRACTS_FILE.read_text())
    config["contracts"].append([new_test])
    registry = tmp_path / "router-contracts.yml"
    registry.write_text(yaml.safe_dump(config, sort_keys=False))
    monkeypatch.setattr(router_shards_module, "ROUTER_CONTRACTS_FILE", registry)
    exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda path: True if path.as_posix() == new_test else exists(path))

    selection = SELECTOR_MODULE["select"]([new_test, ".github/router-contracts.yml"])

    assert router_paths(selection["llm_router_playbooks"]) == [new_test]
    assert len(selection["llm_router_playbooks"]) == 1
    assert "tests/test_router_shard_weights.py" in selection["pytest_targets"]
    assert "tests/repo_guards/test_router_contract_batches.py" in selection["pytest_targets"]


def test_changed_default_used_by_sibling_default_selects_full_router_suite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = "roles/llm_router/defaults/main/56a-target-keys.yml"
    key = "_llm_router_claude_ocg_model_scope"
    versions = {
        "base": f"{key}: old-value\n",
        "head": f"{key}: new-value\n",
    }
    monkeypatch.setattr(router_shards_module, "git_file_at_ref", lambda ref, _: versions[ref])

    selection = SELECTOR_MODULE["select"]([defaults], "base", "head")
    full = json.loads(run_selector("--full").stdout)

    assert selection == full
    assert "tests/llm_router/test_ladder_keys.yml" in router_paths(selection["llm_router_playbooks"])


def test_router_default_referenced_in_role_tasks_selects_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = "roles/llm_router/defaults/main/59-key-scopes.yml"
    key = "_llm_router_private_zdr_model_scope"
    versions = {"base": f"{key}: old\n", "head": f"{key}: new\n"}
    monkeypatch.setattr(router_shards_module, "git_file_at_ref", lambda ref, _: versions[ref])

    selection = SELECTOR_MODULE["select"]([defaults], "base", "head")
    full = json.loads(run_selector("--full").stdout)

    assert selection == full


def test_router_default_with_no_proven_references_selects_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = "roles/llm_router/defaults/main/00-identity.yml"
    key = "_router_default_without_any_contract_reference"
    versions = {"base": f"{key}: old\n", "head": f"{key}: new\n"}
    monkeypatch.setattr(router_shards_module, "git_file_at_ref", lambda ref, _: versions[ref])

    selection = SELECTOR_MODULE["select"]([defaults], "base", "head")
    full = json.loads(run_selector("--full").stdout)

    assert selection == full


def test_computed_lookup_name_in_contract_selects_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults = "roles/llm_router/defaults/main/00-identity.yml"
    key = "_router_dynamic_lookup_scope"
    versions = {"base": f"{key}: old\n", "head": f"{key}: new\n"}
    content = f"{{{{ {key} | default(lookup('vars', 'prefix' ~ suffix)) }}}}"
    monkeypatch.setattr(router_shards_module, "git_file_at_ref", lambda ref, _: versions[ref])
    monkeypatch.setattr(
        router_shards_module,
        "_contract_sources",
        lambda playbook, changed: ([(playbook, content)], False),
    )

    selection = SELECTOR_MODULE["select"]([defaults], "base", "head")
    full = json.loads(run_selector("--full").stdout)

    assert selection == full


def test_unparseable_router_defaults_selects_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = "roles/llm_router/defaults/main/00-identity.yml"
    versions = {"base": "llm_router_user: old\n", "head": "llm_router_user: [broken\n"}
    monkeypatch.setattr(router_shards_module, "git_file_at_ref", lambda ref, _: versions[ref])

    selection = SELECTOR_MODULE["select"]([defaults], "base", "head")
    full = json.loads(run_selector("--full").stdout)

    assert selection["pytest_targets"] == full["pytest_targets"] == ["tests/"]
    assert selection["ansible_tests"] == full["ansible_tests"]
    assert selection["llm_router_playbooks"] == full["llm_router_playbooks"]


def test_router_workflow_edit_selects_every_router_contract() -> None:
    selection = SELECTOR_MODULE["select"]([".github/workflows/_llm-router-contract.yml"])
    full = json.loads(run_selector("--full").stdout)

    assert selection["llm_router_playbooks"] == full["llm_router_playbooks"]


def test_six_or_fewer_router_playbooks_run_in_one_job() -> None:
    six = [[f"tests/llm_router/test_alpha_{index}.yml"] for index in range(6)]
    seven = [[f"tests/llm_router/test_alpha_{index}.yml"] for index in range(7)]

    one_job = router_shards(six, shard_target=1)
    weighted_jobs = router_shards(seven, shard_target=1)

    assert len(one_job) == 1
    assert router_paths(one_job) == sorted(path for entry in six for path in entry)
    assert len(weighted_jobs) > 1


@pytest.mark.parametrize("path", [
    "tests/llm_router/test_gpu_registry.py",
    "tests/llm_router/test_gpu_registry_projection.py",
])
def test_changed_router_pytest_selects_router_contract_job(path: str) -> None:
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert selection["llm_router_playbooks"]


@pytest.mark.parametrize("playbook", [
    "tests/llm_router/test_drift_live_keys.yml",
    "tests/llm_router/test_hermes_public_key_alias_brain.yml",
    "tests/llm_router/test_zcode_sandbox_route_exclusions.yml",
    "tests/llm_router/test_virtual_key_sub_retains_matrix.yml",
])
def test_registered_router_playbook_selects_its_own_entry(playbook: str) -> None:
    result = run_selector(playbook)

    assert result.returncode == 0, result.stderr
    shards = json.loads(result.stdout)["llm_router_playbooks"]
    assert router_paths(shards) == [playbook]


@pytest.mark.parametrize(("imported", "importer"), [
    ("tests/llm_router/test_ladder_keys_order.yml", "tests/llm_router/test_ladder_keys.yml"),
    ("tests/llm_router/test_ladder_keys_consumers.yml", "tests/llm_router/test_ladder_keys.yml"),
    ("tests/llm_router/test_ladder_keys_consumers_permissions.yml", "tests/llm_router/test_ladder_keys.yml"),
    ("tests/llm_router/test_ladder_keys_zdr.yml", "tests/llm_router/test_ladder_keys.yml"),
    ("tests/llm_router/test_gpu_pro6000_inactive_render.yml",
     "tests/llm_router/test_gpu_pro6000_render.yml"),
])
def test_imported_router_playbook_selects_its_importing_entry(imported: str, importer: str) -> None:
    result = run_selector(imported)

    assert result.returncode == 0, result.stderr
    entries = json.loads(result.stdout)["llm_router_playbooks"]
    assert len(entries) == 1
    assert importer in entries[0]["playbooks"].split()


@pytest.mark.parametrize("path", [
    "roles/llm_router/tasks/main.yml",
    "roles/llm_router/defaults/main/59-key-scopes.yml",
])
def test_llm_router_role_paths_select_the_complete_router_contract(path: str) -> None:
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    full = json.loads(run_selector("--full").stdout)
    assert selection["llm_router_playbooks"] == full["llm_router_playbooks"]
    if path.startswith("roles/llm_router/defaults/main/"):
        assert selection["pytest_targets"] == ["tests/"]
    else:
        assert {"tests/llm_gpu_engine_roles/", "tests/hermes_agent/"} <= set(selection["pytest_targets"])


def test_full_suite_keeps_every_router_matrix_entry() -> None:
    result = subprocess.run(
        [sys.executable, str(SELECTOR), "--full"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    selection = json.loads(result.stdout)
    assert len(selection["llm_router_playbooks"]) <= 16
    assert sorted(router_paths(selection["llm_router_playbooks"])) == sorted(
        path for entry in GENERATED_MATRIX for path in entry)


def test_router_shards_pack_topics_and_split_only_oversized_topics() -> None:
    paths = [f"tests/llm_router/test_alpha_{index}_contract.yml" for index in range(3)]
    paths.extend(f"tests/llm_router/test_topic{index}_contract.yml" for index in range(14))
    shards = router_shards([[path] for path in paths])
    alpha = [shard for shard in shards if "alpha " in shard["name"]]

    assert len(shards) <= 16
    assert len(alpha) == 2
    assert {shard["name"].split(", ", 1)[0] for shard in alpha} == {"alpha 1/2", "alpha 2/2"}
    assert any(", " in shard["name"] for shard in shards)
    assert max(sum(router_weight(path) for path in shard["playbooks"].split())
               for shard in shards) <= router_shard_target([[path] for path in paths])
    assert sorted(router_paths(shards)) == sorted(paths)


def test_changed_python_router_selector_test_selects_only_itself() -> None:
    path = "tests/test_contract_scope_router_selector.py"
    result = run_selector(path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pytest_targets"] == [path]
