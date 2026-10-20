"""The pre-download stop reaches serving units outside the profile map."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pre_download_stop_covers_installed_units_outside_the_map() -> None:
    tasks = yaml.safe_load((REPO_ROOT / "roles/llm_gpu_serving/tasks/cache-sync.yml").read_text(encoding="utf-8"))
    find = next(task for task in tasks if task["name"].startswith("Find every installed GPU serving unit"))
    stop = next(task for task in tasks if task["name"].startswith("Stop this role's serving units"))
    register = find["register"]
    assert register in stop["loop"]
    assert "llm_gpu_serving_profiles" in stop["loop"]
    assert find["when"] == stop["when"]
