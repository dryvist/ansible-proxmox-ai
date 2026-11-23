"""Contract tests for gbench support in llm-model-campaign."""

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "playbooks" / "tasks"


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_gbench_preflight_and_summary_discovery_contracts() -> None:
    preflight_tasks = _load_tasks(TASKS / "llm-model-campaign-preflight-tool.yml")
    surface = _named(preflight_tasks, "Inspect the installed gbench command surface")
    options = _named(preflight_tasks, "Require every gbench option used by the selected recipe")

    assert surface["ansible.builtin.command"]["argv"] == ["gbench", "--help"]
    assert "localhost" in surface["delegate_to"]
    assert surface["when"] == "_llm_campaign_run.executable == 'gbench'"
    assert options["when"] == "_llm_campaign_run.executable == 'gbench'"
    required = [c.split("search('", 1)[1].split("')", 1)[0] for c in options["ansible.builtin.assert"]["that"]]
    assert {"--remote-endpoint", "--golden-only", "--stress-test", "--evals-only", "--results-dir"} <= set(
        required
    )

    cell_tasks = _load_tasks(TASKS / "llm-model-campaign-cell.yml")
    find_gb = _named(cell_tasks, "Find gbench summary files for its converter")
    fetch_gb = _named(cell_tasks, "Fetch gbench summary files for conversion")
    find_local = _named(cell_tasks, "Find local converter inputs for this cell")

    assert find_gb["ansible.builtin.find"]["patterns"] == ["summary.json"]
    assert find_gb["ansible.builtin.find"]["recurse"] is True
    assert "_llm_campaign_run.run_on != 'controller'" in fetch_gb["when"]
    assert "'summary.json'" in find_local["ansible.builtin.find"]["patterns"]
