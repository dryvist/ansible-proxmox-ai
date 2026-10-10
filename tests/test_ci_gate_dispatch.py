from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined

SELECTOR = Path(__file__).parents[1] / ".github/scripts/select-contract-scope.py"
CI_GATE = Path(__file__).parents[1] / ".github/workflows/ci-gate.yml"
# The router matrix as the selector generates it from the workflow.
GENERATED_MATRIX = runpy.run_path(str(SELECTOR))["llm_router_matrix"]()


def run_selector(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SELECTOR), *paths], check=False, capture_output=True, text=True)


def test_full_suite_covers_main_and_develop_pushes_and_promotion_prs() -> None:
    workflow = CI_GATE.read_text()

    assert 'EVENT_NAME" == push && "$GITHUB_REF" == refs/heads/main' in workflow
    assert 'EVENT_NAME" == push && "$GITHUB_REF" == refs/heads/develop' in workflow
    assert 'BASE_SHA="$PUSH_BEFORE"' in workflow
    assert 'HEAD_SHA="$PUSH_HEAD"' in workflow
    molecule = yaml.safe_load(workflow)["jobs"]["molecule"]
    assert "github.event.pull_request.base.ref == 'main'" in molecule["with"]["full_suite"]
    assert molecule["if"].count("github.event.pull_request.base.ref == 'main'") == 2
    assert "inputs.full_suite && steps.find.outputs.scenarios" in (
        CI_GATE.parents[0] / "_molecule.yml"
    ).read_text()
    matrix_edit = json.loads(run_selector(".github/workflows/_llm-router-contract.yml").stdout)
    full = json.loads(run_selector("--full").stdout)
    assert matrix_edit["run_selector_checks"]
    assert set(matrix_edit["llm_router_playbooks"]) == set(full["llm_router_playbooks"])
    assert "tests/test_contract_scope_selector.py" in workflow


@pytest.mark.parametrize(("event", "ref", "base", "expected_full"), [
    ("pull_request", "refs/pull/1/merge", "main", True),
    ("pull_request", "refs/pull/1/merge", "develop", False),
    ("push", "refs/heads/main", "", True),
    ("push", "refs/heads/develop", "", True),
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
    result = subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    selected = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert selected["full_suite"] == str(expected_full).lower()
    targets = json.loads(selected["pytest_targets"])
    assert targets == (["tests/"] if expected_full else ["tests/test_contract_scope_selector.py"])
    assert len(json.loads(selected["llm_router_playbooks"])) == (len(GENERATED_MATRIX) if expected_full else 0)


@pytest.mark.parametrize(("event", "ref", "base", "allows_skips"), [
    ("pull_request", "refs/pull/1/merge", "main", False),
    ("pull_request", "refs/pull/1/merge", "develop", True),
    ("push", "refs/heads/main", "", True),
    ("push", "refs/heads/develop", "", True),
])
def test_actual_gate_policy_allows_skips_only_for_focused_and_push_runs(
    event: str, ref: str, base: str, allows_skips: bool,
) -> None:
    gate = yaml.safe_load(CI_GATE.read_text())["jobs"]["gate"]
    step = next(step for step in gate["steps"] if step.get("name") == "Check all results")
    clause = step["env"]["CI_GATE_ALLOWED_SKIPS"].strip().removeprefix("${{").removesuffix("}}")
    clause = re.sub(r"!(?!=)", "not ", clause.replace("&&", "and").replace("||", "or"))
    allowed = Environment(undefined=StrictUndefined).compile_expression(clause)(github={
        "event_name": event, "ref": ref, "event": {"pull_request": {"base": {"ref": base}}},
    })
    assert isinstance(allowed, str)
    allowed_set = {name.strip() for name in allowed.split(",") if name.strip()}
    required = {"data-contract", "molecule"}
    assert required <= set(gate["needs"])
    assert (required <= allowed_set) is allows_skips
    assert bool(required - allowed_set) is (not allows_skips)
