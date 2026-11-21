from __future__ import annotations

from pathlib import Path

import yaml

CI_FIX = Path(__file__).parents[1] / ".github/workflows/ci-fix.yml"


def test_openhands_ci_fix_runs_on_failure_or_per_run_dispatch() -> None:
    workflow = yaml.safe_load(CI_FIX.read_text())
    inputs = workflow[True]["workflow_dispatch"]["inputs"]  # PyYAML reads the `on` key as True
    assert all(inputs[name]["required"] for name in ("failure_run_id", "failure_head_branch"))
    job = workflow["jobs"]["ci-fix"]
    assert job["if"] == ("github.event_name == 'workflow_dispatch' || "
                         "github.event.workflow_run.conclusion == 'failure'")
    assert job["with"]["failure_run_id"] == "${{ inputs.failure_run_id }}"
    assert job["with"]["failure_head_branch"] == "${{ inputs.failure_head_branch }}"
