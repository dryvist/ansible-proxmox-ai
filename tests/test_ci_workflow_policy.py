from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

CI_GATE = Path(__file__).parents[1] / ".github/workflows/ci-gate.yml"
WORKFLOWS = Path(__file__).parents[1] / ".github/workflows"


def _job_steps(workflow: str, job: str) -> list[dict]:
    """A job's steps with local composite actions (`uses: ./...`) expanded in place."""
    def expand(steps: list[dict]) -> list[dict]:
        out = []
        for step in steps:
            uses = step.get("uses", "")
            if uses.startswith("./"):
                action = yaml.safe_load((WORKFLOWS.parents[1] / uses / "action.yml").read_text())
                out.extend(expand(action["runs"]["steps"]))
            else:
                out.append(step)
        return out
    return expand(yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"][job]["steps"])


def test_agent_ci_fix_runs_only_for_pull_request_ci_gate_runs() -> None:
    job = yaml.safe_load((WORKFLOWS / "agent-ci-fix.yml").read_text())["jobs"]["ci-fix"]
    assert "vars.AI_AGENT_CI_FIX_ENABLED == 'true'" in job["if"]
    assert "github.event.workflow_run.event == 'pull_request'" in job["if"]


@pytest.mark.parametrize("workflow", ["agent-ci-fix.yml", "agent-pr-review-responder.yml"])
def test_agent_workflows_use_the_central_v1_callee(workflow: str) -> None:
    jobs = yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"].values()
    uses = [job["uses"] for job in jobs if "uses" in job]
    assert uses
    assert all(re.fullmatch(r"JacobPEvans/.github-ai/\.github/workflows/[\w.-]+@v1", ref)
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
def test_push_events_skip_focused_contract_and_molecule_jobs(job: str, guard: str) -> None:
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


# The one per-scenario exception: hermes_ui gets at least 25 minutes, never less than the caller's.
HERMES_UI_TIMEOUT = (
    "${{ (matrix.scenario == 'hermes_ui' && inputs.timeout_minutes < 25) && 25 || inputs.timeout_minutes }}"
)
ROUTER_TIMEOUT = "${{ inputs.timeout_minutes < 30 && 30 || inputs.timeout_minutes }}"
ROUTER_DEPENDENCIES_TIMEOUT = "${{ github.event_name == 'pull_request' && 5 || inputs.timeout_minutes }}"
# The broad pytest job documents a timeout-exception in the workflow: the suite plus a cold install passes five.
ROLE_SELF_CHECKS_TIMEOUT = "${{ github.event_name == 'pull_request' && 10 || inputs.timeout_minutes }}"
# The merge gate only aggregates results, so it takes the shared policy's five-minute PR cap.
GATE_TIMEOUT = "${{ github.event_name == 'pull_request' && 5 || 10 }}"


@pytest.mark.parametrize(("scenario", "caller", "expected"), [
    ("hermes_ui", 15, 25),
    ("hermes_ui", 60, 60),
    ("qdrant", 15, 15),
    ("qdrant", 60, 60),
])
def test_hermes_ui_timeout_only_raises_hermes_ui(scenario: str, caller: int, expected: int) -> None:
    context = {"matrix.scenario": scenario, "inputs.timeout_minutes": caller}
    assert _evaluate_timeout(HERMES_UI_TIMEOUT, context) == expected


@pytest.mark.parametrize(("caller", "expected"), [(15, 30), (60, 60)])
def test_router_contract_timeout_is_thirty_or_preserves_the_caller(caller: int, expected: int) -> None:
    assert _evaluate_timeout(ROUTER_TIMEOUT, {"inputs.timeout_minutes": caller}) == expected


@pytest.mark.parametrize(("event", "caller", "expected"), [
    ("pull_request", 10, 5),
    ("workflow_call", 60, 60),
])
def test_router_contract_dependency_timeout_is_five_for_pull_requests(
    event: str, caller: int, expected: int
) -> None:
    context = {"github.event_name": event, "inputs.timeout_minutes": caller}
    assert _evaluate_timeout(ROUTER_DEPENDENCIES_TIMEOUT, context) == expected


@pytest.mark.parametrize("path", sorted(WORKFLOWS.glob("*.yml")), ids=lambda path: path.name)
def test_every_runner_job_takes_the_promotion_timeout(path: Path) -> None:
    for name, job in yaml.safe_load(path.read_text())["jobs"].items():
        if "runs-on" in job and (path.name, name) != ("fix-installer-sha.yml", "fix"):
            value = " ".join(str(job.get("timeout-minutes")).split())
            allowed = {PROMOTION_TIMEOUT, "${{ inputs.timeout_minutes }}"}
            if (path.name, name) == ("_molecule.yml", "test"):
                allowed = {HERMES_UI_TIMEOUT}
            elif (path.name, name) == ("_llm-router-contract.yml", "router-contract-dependencies"):
                allowed = {ROUTER_DEPENDENCIES_TIMEOUT}
            elif (path.name, name) == ("_llm-router-contract.yml", "llm-router-contract"):
                allowed = {ROUTER_TIMEOUT}
            elif (path.name, name) == ("_data-contract.yml", "role-self-checks"):
                allowed = {ROLE_SELF_CHECKS_TIMEOUT}
            elif (path.name, name) == ("ci-gate.yml", "gate"):
                allowed = {GATE_TIMEOUT}
            assert value in allowed, f"{path.name}:{name}"


def test_reusable_calls_forward_the_timeout_input() -> None:
    jobs = yaml.safe_load(CI_GATE.read_text())["jobs"]
    for name in ("data-contract", "molecule"):
        timeout = " ".join(jobs[name]["with"]["timeout_minutes"].split())
        assert timeout == PROMOTION_TIMEOUT.replace("|| 10", "|| 15"), name
    nested = yaml.safe_load((WORKFLOWS / "_data-contract.yml").read_text())["jobs"]["llm-router-contract"]
    assert nested["with"]["timeout_minutes"] == "${{ inputs.timeout_minutes }}"


@pytest.mark.parametrize(("workflow", "job"), [
    ("_data-contract.yml", "syntax-check"),
    ("_data-contract.yml", "verify-inventory-load"),
])
def test_ansible_install_uses_cached_pinned_ci_requirements(workflow: str, job: str) -> None:
    steps = _job_steps(workflow, job)
    setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["cache"] == "pip"
    assert setup["with"]["cache-dependency-path"] == ".github/requirements-ci.txt"
    galaxy_cache = next(step for step in steps if step.get("id") == "galaxy-cache")
    assert "hashFiles('requirements.yml')" in galaxy_cache["with"]["key"]
    galaxy_install = next(step for step in steps if step.get("uses", "").startswith("nick-fields/retry@"))
    assert galaxy_install["if"] == "steps.galaxy-cache.outputs.cache-hit != 'true'"
    assert galaxy_install["with"]["command"] == "ansible-galaxy install --force -r requirements.yml"
    install = next(step for step in steps if "pip install" in step.get("run", ""))
    assert "pip install -r .github/requirements-ci.txt" in install["run"]


@pytest.mark.parametrize(("workflow", "job"), [
    ("_data-contract.yml", "syntax-check"),
    ("_data-contract.yml", "verify-inventory-load"),
    ("_molecule.yml", "test"),
])
def test_galaxy_install_skips_exact_cache_hits(workflow: str, job: str) -> None:
    steps = _job_steps(workflow, job)
    cache = next(step for step in steps if step.get("id") == "galaxy-cache")
    install = next(step for step in steps if "ansible-galaxy install --force -r requirements.yml"
                   == step.get("with", {}).get("command"))
    assert cache["uses"].startswith("actions/cache@")
    assert install["if"] == "steps.galaxy-cache.outputs.cache-hit != 'true'"


@pytest.mark.parametrize("job", [
    "router-contract-dependencies", "llm-router-contract", "llm-router-registry-contract",
])
def test_router_jobs_share_the_cached_dependency_action(job: str) -> None:
    steps = yaml.safe_load((WORKFLOWS / "_llm-router-contract.yml").read_text())["jobs"][job]["steps"]
    assert any(step.get("uses") == "./.github/actions/router-deps" for step in steps)


def test_router_dependency_action_caches_pinned_requirements() -> None:
    steps = yaml.safe_load((WORKFLOWS.parent / "actions/router-deps/action.yml").read_text())["runs"]["steps"]
    setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["cache"] == "pip"
    assert setup["with"]["cache-dependency-path"] == ".github/requirements-ci.txt"
    key = next(step for step in steps if step.get("id") == "key")
    assert key["env"]["HASH"] == "${{ hashFiles('.github/requirements-ci.txt', 'requirements.yml') }}"
    restore = next(step for step in steps if step.get("id") == "router-deps-cache")
    assert restore["with"]["key"] == "${{ steps.key.outputs.key }}"
    install = next(step for step in steps if step.get("uses", "").startswith("nick-fields/retry@"))
    assert install["if"] == "steps.router-deps-cache.outputs.cache-hit != 'true'"


def test_ci_requirements_pin_exact_versions() -> None:
    lines = [line.strip() for line in (WORKFLOWS.parents[0] / "requirements-ci.txt").read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    assert any(line.startswith("ansible==") for line in lines)
    assert all("==" in line for line in lines)
