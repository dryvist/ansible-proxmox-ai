from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = ROOT / "playbooks"
PAIRED_PASSES = PLAYBOOKS / "tasks/llm-model-campaign-paired-passes.yml"


def _tasks(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _paired_block() -> dict:
    return next(task for task in _tasks(PAIRED_PASSES) if "block" in task)


def test_paired_entry_point_loads_inventory_and_enables_paired_mode() -> None:
    playbook = _tasks(PLAYBOOKS / "llm-model-paired-campaign.yml")

    assert playbook[0]["ansible.builtin.import_playbook"] == "../inventory/load_tofu.yml"
    campaign = playbook[1]
    assert campaign["ansible.builtin.import_playbook"] == "llm-model-campaign.yml"
    assert campaign["vars"] == {"llm_model_campaign_paired_trial": True}


def test_target_runs_the_pass_file_for_both_modes() -> None:
    target = _tasks(PLAYBOOKS / "llm-model-campaign-target.yml")[0]
    include = next(task for task in target["tasks"] if "ansible.builtin.include_tasks" in task)

    assert include["ansible.builtin.include_tasks"] == "tasks/llm-model-campaign-paired-passes.yml"


def test_paired_passes_share_one_lock_and_release_in_always() -> None:
    paired = _paired_block()
    names = [task["name"] for task in paired["block"]]

    assert names.index("Acquire the trial lock before the baseline pass") < names.index("Run the baseline campaign pass")
    assert names.index("Run the baseline campaign pass") < names.index("Hold the candidate service for the second paired pass")
    assert names.index("Hold the candidate service for the second paired pass") < names.index("Run the candidate campaign pass against the trial endpoint")
    assert paired["block"][0]["ansible.builtin.include_role"]["tasks_from"] == "lock.yml"
    assert paired["block"][2]["ansible.builtin.include_role"]["tasks_from"] == "hold.yml"
    candidate = paired["block"][4]
    assert candidate["vars"]["llm_model_campaign_pass_name"] == "candidate"
    assert candidate["vars"]["llm_model_campaign_allow_trial_http"] is True
    assert paired["always"][0]["block"][0]["ansible.builtin.include_role"]["tasks_from"] == "release.yml"


def test_campaign_output_dir_is_optional_and_scoped_by_pass() -> None:
    inputs = (PLAYBOOKS / "tasks/llm-model-campaign-paired-inputs.yml").read_text(encoding="utf-8")
    converter = (PLAYBOOKS / "tasks/llm-model-campaign-convert.yml").read_text(encoding="utf-8")

    assert "llm_model_campaign_output_dir | default('')" in inputs
    assert "['--output-dir', _llm_campaign_output_dir ~ '/' ~ llm_model_campaign_pass_name]" in converter


def test_verdict_reads_every_repetition_and_scores_shared_metric_keys() -> None:
    verdict = (PLAYBOOKS / "tasks/llm-model-campaign-verdict.yml").read_text(encoding="utf-8")
    facts = (PLAYBOOKS / "tasks/llm-model-campaign-verdict-facts.yml").read_text(encoding="utf-8")

    assert "*.json" in verdict
    assert "matched == 1" not in verdict
    assert "^(acc|exact_match)" in facts
    assert "_llm_campaign_common_keys" in facts
    assert "no metric key is common to both passes" in facts
    assert "_llm_campaign_tolerance | string" in facts
    assert "_llm_campaign_candidate_score >= _llm_campaign_baseline_score - _llm_campaign_tolerance | float" in facts


def test_invalid_verdict_fails_only_after_verdict_json_is_written() -> None:
    tasks = _tasks(PLAYBOOKS / "tasks/llm-model-campaign-verdict.yml")
    write = next(i for i, task in enumerate(tasks) if "ansible.builtin.copy" in task)
    fail = next(i for i, task in enumerate(tasks) if "ansible.builtin.fail" in task)

    assert write < fail
    assert tasks[fail]["when"] == "llm_model_paired_trial_verdict.status == 'invalid'"


def test_paired_release_keeps_restore_verification_and_records_failure() -> None:
    release = _paired_block()["always"][0]

    assert "llm_model_trial_require_restore_verification" not in PAIRED_PASSES.read_text(encoding="utf-8")
    assert release["block"][0]["ansible.builtin.include_role"]["tasks_from"] == "release.yml"
    assert release["rescue"][-1]["ansible.builtin.set_fact"] == {"_llm_campaign_release_failed": True}


def test_each_pass_carries_its_own_model_identity() -> None:
    paired = _paired_block()
    baseline = paired["block"][1]["vars"]
    candidate = paired["block"][4]["vars"]

    assert baseline["llm_model_campaign_pass_hf_repo"] == "{{ _llm_campaign_model.hf_repo }}"
    assert baseline["llm_model_campaign_pass_serving_model"] == "{{ _llm_campaign_serving_model }}"
    assert candidate["llm_model_campaign_pass_hf_repo"] == "{{ hf_repo }}"
    assert candidate["llm_model_campaign_pass_serving_model"] == "{{ hf_repo }}"
    cell = (PLAYBOOKS / "tasks/llm-model-campaign-cell.yml").read_text(encoding="utf-8")
    converter = (PLAYBOOKS / "tasks/llm-model-campaign-convert.yml").read_text(encoding="utf-8")
    assert "'model_id': llm_model_campaign_pass_hf_repo" in cell
    assert "'--model', llm_model_campaign_pass_hf_repo" in converter
    assert "'--serving-model', llm_model_campaign_pass_serving_model" in converter
