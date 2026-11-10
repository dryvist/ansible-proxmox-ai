"""The container-trial playbook converges only its opt-in group and never the live medium guest."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK = ROOT / "playbooks/llm-gpu-container-trial.yml"
LIVE_MEDIUM = "llm-blackwell-7-96GB-medium"


def _play() -> dict:
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    assert len(plays) == 2
    return plays[1]


def test_play_targets_only_the_opt_in_group_one_guest_at_a_time():
    play = _play()
    assert play["hosts"] == "{{ llm_gpu_container_trial_hosts | default('llm_gpu_container_trial_group') }}"
    assert play["serial"] == 1
    assert play["any_errors_fatal"] is True


def test_play_applies_docker_then_the_toolkit_role():
    roles = [
        task["ansible.builtin.include_role"]["name"]
        for task in _play()["tasks"]
        if "ansible.builtin.include_role" in task
    ]
    assert roles == ["docker_engine", "nvidia_container_toolkit"]


def test_playbook_loads_the_inventory_first():
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    assert plays[0]["ansible.builtin.import_playbook"] == "../inventory/load_tofu.yml"


def test_playbook_refuses_the_live_medium_guest_first():
    first = _play()["pre_tasks"][0]["ansible.builtin.assert"]
    assert f"inventory_hostname != '{LIVE_MEDIUM}'" in first["that"]


def test_no_other_playbook_imports_the_container_trial():
    playbook_name = PLAYBOOK.name
    for path in sorted((ROOT / "playbooks").rglob("*.yml")):
        if path == PLAYBOOK:
            continue
        text = path.read_text(encoding="utf-8")
        assert playbook_name not in text, path
        assert "nvidia_container_toolkit" not in text, path
    for path in [ROOT / "playbooks/site.yml", ROOT / "playbooks/llm-serving.yml", ROOT / "playbooks/llm-serving-gpu-engines.yml"]:
        assert "llm_gpu_container_trial_group" not in path.read_text(encoding="utf-8"), path
