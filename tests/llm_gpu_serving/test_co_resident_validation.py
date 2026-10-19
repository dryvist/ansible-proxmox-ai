"""The co-resident validation accepts the main profile the medium GPU host selects."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_co_resident_validation_leaves_slot_count_to_the_profile() -> None:
    host_vars = yaml.safe_load(
        next((REPO_ROOT / "inventory/host_vars").glob("llm-blackwell-*-medium.yml")).read_text(encoding="utf-8")
    )
    assert host_vars["llm_gpu_serving_co_resident_profiles"]
    tasks = yaml.safe_load((REPO_ROOT / "roles/llm_gpu_serving/tasks/validate-profiles.yml").read_text(encoding="utf-8"))
    checks = next(
        task["ansible.builtin.assert"]["that"]
        for task in tasks
        if task["name"] == "Validate the selected co-resident GPU profiles"
    )
    assert not [check for check in checks if "max_num_seqs" in check]
