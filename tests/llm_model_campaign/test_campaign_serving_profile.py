"""Check the explicit campaign serving-profile override and its artifact guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Template
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOKS = REPO_ROOT / "playbooks"
TASKS = PLAYBOOKS / "tasks"
TARGET_PLAYBOOK = PLAYBOOKS / "llm-model-campaign-target.yml"


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _named(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_campaign_serving_profile_defaults_to_artifact_and_selects_override_unit():
    controller = yaml.safe_load((PLAYBOOKS / "llm-model-campaign.yml").read_text(encoding="utf-8"))
    selection = _named(controller[0]["tasks"], "Resolve the campaign serving profile")
    selection_template = selection["ansible.builtin.set_fact"]["_llm_campaign_selected_serving_profile"]

    default_profile = Template(selection_template).render(
        llm_model_campaign_serving_profile="",
        _llm_campaign_selected_artifacts=[{"serving_profile": "artifact-profile"}],
    )
    override_profile = Template(selection_template).render(
        llm_model_campaign_serving_profile="explicit-profile",
        _llm_campaign_selected_artifacts=[{"serving_profile": "artifact-profile"}],
    )
    assert default_profile == "artifact-profile"
    assert override_profile == "explicit-profile"

    target = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))[0]
    unit_task = _named(target["pre_tasks"], "Record the selected serving profile systemd unit")
    unit = Template(unit_task["ansible.builtin.set_fact"]["_llm_campaign_serving_unit"]).render(
        _llm_campaign_role_defaults={"llm_gpu_serving_unit_prefix": "llm-gpu-serving"},
        _llm_campaign_serving_profile=override_profile,
    )
    assert unit == "llm-gpu-serving-explicit-profile.service"
    assert not any(character.isspace() for character in unit)


def test_campaign_profile_join_rejects_a_different_artifact_before_target_work():
    plays = yaml.safe_load((PLAYBOOKS / "llm-model-campaign.yml").read_text(encoding="utf-8"))
    controller = plays[0]
    tasks = controller["tasks"]
    include = _named(tasks, "Resolve and validate an explicit campaign serving profile")
    save = _named(tasks, "Save the prepared campaign inputs for the inventory target play")
    profile_tasks = _load_tasks(TASKS / "llm-model-campaign-serving-profile.yml")
    profile_loader = _named(profile_tasks, "Load the shared vLLM profile map for an explicit campaign profile")
    registry_loader = _named(profile_tasks, "Load GPU profile registry entries for an explicit campaign profile")
    guard = _named(profile_tasks, "Validate the explicit campaign profile against its artifact and engine")

    assert controller["hosts"] == "localhost"
    assert include["ansible.builtin.include_tasks"] == "tasks/llm-model-campaign-serving-profile.yml"
    assert "llm_model_campaign_serving_profile" in include["when"]
    assert tasks.index(include) < tasks.index(save)
    assert profile_loader["ansible.builtin.include_vars"]["dir"].endswith(
        "roles/vllm_serving/defaults/main"
    )
    assert registry_loader["ansible.builtin.include_vars"]["dir"].endswith("llm-models.d")
    assert (
        profile_tasks.index(profile_loader) < profile_tasks.index(registry_loader) < profile_tasks.index(guard)
    )
    assert plays[-1]["ansible.builtin.import_playbook"] == "llm-model-campaign-target.yml"

    guard_condition = guard["ansible.builtin.assert"]["that"][0]
    rejected = Template("{{ " + guard_condition + " }}").render(
        _llm_campaign_selected_serving_profile="registered-profile",
        vllm_serving_profiles={"registered-profile": {"enabled": True, "engine": "vllm"}},
        _llm_campaign_selected_serving_profile_records=[{"artifact_id": "registered-artifact"}],
        _llm_campaign_selected_artifacts=[
            {"artifact_id": "requested-artifact", "use": "serving", "engines": ["vllm"]}
        ],
        engine="vllm",
    )
    assert rejected == "False"
    assert "artifact {{ artifact_id }}" in guard["ansible.builtin.assert"]["fail_msg"]

    target = yaml.safe_load(TARGET_PLAYBOOK.read_text(encoding="utf-8"))[0]
    profile_limits = _named(target["pre_tasks"], "Require survey axes to fit explicit serving profile limits")
    assert "max_model_len" in str(profile_limits["ansible.builtin.assert"]["that"])
    assert "max_num_seqs" in str(profile_limits["ansible.builtin.assert"]["that"])
