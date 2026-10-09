from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

CI_GATE = Path(__file__).parents[1] / ".github/workflows/ci-gate.yml"
WORKFLOWS = Path(__file__).parents[1] / ".github/workflows"


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


@pytest.mark.parametrize(("job", "guard"), [
    ("data-contract", "github.event_name != 'push'"),
    ("molecule", "github.event_name == 'pull_request'"),
])
def test_push_events_skip_the_contract_and_molecule_jobs(job: str, guard: str) -> None:
    condition = yaml.safe_load(CI_GATE.read_text())["jobs"][job]["if"]
    assert guard in condition


PROMOTION_TIMEOUT = (
    "${{ (github.event_name == 'pull_request' && github.event.pull_request.base.ref == 'main' "
    "&& github.event.pull_request.head.ref == 'develop' "
    "&& github.event.pull_request.head.repo.full_name == github.repository) && 60 || 10 }}"
)


def _context(
    event: str, base: str | None = None, head: str | None = None, head_repo: str | None = None
) -> dict[str, str | None]:
    return {
        "github.event_name": event,
        "github.event.pull_request.base.ref": base,
        "github.event.pull_request.head.ref": head,
        "github.event.pull_request.head.repo.full_name": head_repo,
        "github.repository": "owner/repo",
    }


def _evaluate_timeout(expression: str, context: dict[str, str | None]) -> object:
    # Substitute the context values, then map the expression's && and || onto Python's and/or.
    body = expression.removeprefix("${{").removesuffix("}}")
    for key, value in context.items():
        body = body.replace(key, repr(value))
    return eval(body.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}, {})


@pytest.mark.parametrize(("context", "expected"), [
    (_context("pull_request", "main", "develop", "owner/repo"), 60),
    (_context("pull_request", "main", "develop", "fork/ansible-proxmox-ai"), 10),
    (_context("pull_request", "main", "release-please--branches--main", "owner/repo"), 10),
    (_context("pull_request", "main", "hotfix/fix-x", "owner/repo"), 10),
    (_context("pull_request", "develop", "feature/x", "owner/repo"), 10),
    (_context("push"), 10),
])
def test_promotion_timeout_is_sixty_only_for_same_repo_develop_into_main(
    context: dict[str, str | None], expected: int
) -> None:
    assert _evaluate_timeout(PROMOTION_TIMEOUT, context) == expected


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_every_runner_job_takes_the_promotion_timeout(path: Path) -> None:
    for name, job in yaml.safe_load(path.read_text())["jobs"].items():
        if "runs-on" in job and (path.name, name) != ("fix-installer-sha.yml", "fix"):
            value = " ".join(str(job.get("timeout-minutes")).split())
            assert value in (PROMOTION_TIMEOUT, "${{ inputs.timeout_minutes }}"), f"{path.name}:{name}"


def test_reusable_calls_forward_the_timeout_input() -> None:
    jobs = yaml.safe_load(CI_GATE.read_text())["jobs"]
    for name in ("data-contract", "molecule"):
        assert " ".join(jobs[name]["with"]["timeout_minutes"].split()) == PROMOTION_TIMEOUT, name
    nested = yaml.safe_load((WORKFLOWS / "_data-contract.yml").read_text())["jobs"]["llm-router-contract"]
    assert nested["with"]["timeout_minutes"] == "${{ inputs.timeout_minutes }}"


@pytest.mark.parametrize(("workflow", "job"), [
    ("_data-contract.yml", "syntax-check"),
    ("_data-contract.yml", "verify-inventory-load"),
    ("_llm-router-contract.yml", "llm-router-contract"),
    ("_llm-router-contract.yml", "llm-router-registry-contract"),
])
def test_ansible_install_uses_the_pinned_ci_requirements(workflow: str, job: str) -> None:
    steps = yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"][job]["steps"]
    setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["cache"] == "pip"
    assert setup["with"]["cache-dependency-path"] == ".github/requirements-ci.txt"
    install = next(step for step in steps if "pip install" in step.get("run", ""))
    assert "pip install -r .github/requirements-ci.txt" in install["run"]


def test_ci_requirements_pin_exact_versions() -> None:
    lines = [line.strip() for line in (WORKFLOWS.parents[0] / "requirements-ci.txt").read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert "ansible==14.0.0" in lines
    assert all("==" in line for line in lines)
