"""Contract tests for gbench execution in playbooks/benchmark-run.yml."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]
PLAYBOOK = ROOT / "playbooks/benchmark-run.yml"
TASKS = ROOT / "playbooks/tasks/benchmark-run"


def load_tasks(name: str) -> list[dict[str, Any]]:
    return yaml.safe_load((TASKS / name).read_text())


def test_gbench_suites_preflight_prepare_and_execute() -> None:
    preflight = load_tasks("preflight.yml")
    suite_assert = "\n".join(preflight[0]["ansible.builtin.assert"]["that"])
    for suite in ("gbench-golden", "gbench-stress-agentic", "gbench-capability-unsat"):
        assert suite in suite_assert

    items_guard = next(t for t in preflight if t.get("name") == "Require no separate input file for gbench suites")
    assert items_guard["when"] == "suite is match('^gbench-')"

    prepare = load_tasks("prepare.yml")
    verify = next(t for t in prepare if t.get("name") == "Verify pinned gbench in selected client")
    assert verify["ansible.builtin.command"]["argv"][-2:] == ["/usr/local/bin/gbench", "--version"]

    execute = load_tasks("execute.yml")
    block = execute[0]["block"]
    dispatch = next(t for t in block if t.get("ansible.builtin.import_tasks") == "gbench.yml")
    assert dispatch["when"] == "benchmark_run_request.suite is match('^gbench-')"

    gbench_tasks = load_tasks("gbench.yml")
    args_task = next(t for t in gbench_tasks if t.get("name") == "Build gbench CLI arguments for the selected suite")
    args_expr = str(args_task["ansible.builtin.set_fact"]["_benchmark_run_gbench_args"])
    for flag in ("--text-only", "--stress-reps", "--stress-certify"):
        assert flag in args_expr

    run_task = next(t for t in gbench_tasks if t.get("name") == "Run the pinned gbench suite in the client")
    assert run_task["no_log"] is True
    assert run_task["failed_when"] == "_benchmark_run_suite.rc not in [0, 1]"

    find_task = next(t for t in gbench_tasks if t.get("name") == "Find the gbench summary.json produced for this run")
    assert "summary.json" in find_task["ansible.builtin.command"]["argv"]
