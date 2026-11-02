"""Contract for the medium vLLM campaign target group and its single-alias check."""

from __future__ import annotations

from test_inventory_contract import REPO_ROOT, _load


def _task(tasks, name):
    return next(task for task in tasks if task.get("name") == name)


def test_medium_vllm_campaign_selector_uses_the_profile_and_started_vllm_guest():
    loader = _load(REPO_ROOT / "inventory/load_tofu/add_lxc_hosts.yml")
    fact = _task(loader, "Record whether the started vLLM guest is the medium campaign target")
    condition = fact["ansible.builtin.set_fact"]["load_tofu_medium_vllm_campaign"]
    add_target = _task(loader, "Add the started vLLM guest to the medium campaign target group")
    names = [task.get("name") for task in loader]

    assert "load_tofu_gpu_engine_set_present | bool" in condition
    assert "load_tofu_gpu_engine_selector == 'vllm'" in condition
    assert "llm_gpu_active_profiles_by_engine.get('vllm', '') is match('^medium')" in condition
    assert add_target["ansible.builtin.add_host"]["groups"] == ["llm_campaign_medium_vllm_target"]
    assert add_target["when"] == [
        "load_tofu_medium_vllm_campaign | bool",
        "item.value.llm_gpu_engine_identity | default('') == 'vllm'",
        "item.value.started | default(false) | bool",
    ]
    assert names.index("Add the started vLLM guest to the medium campaign target group") > names.index(
        "Add AI LXC containers to inventory"
    )


def test_only_the_campaign_requires_exactly_one_medium_vllm_alias():
    loader = _load(REPO_ROOT / "inventory/load_tofu/add_lxc_hosts.yml")
    campaign = _load(REPO_ROOT / "playbooks/llm-model-campaign.yml")
    target_assert = _task(
        campaign[0]["tasks"], "Require one inventory alias when the medium vLLM campaign selector is used"
    )["ansible.builtin.assert"]

    assert "groups['llm_campaign_medium_vllm_target'] | length == 1" in target_assert["that"][0]
    assert "exactly one inventory" in target_assert["fail_msg"]
    assert not any("ansible.builtin.assert" in task and "llm_campaign_medium_vllm_target" in str(task) for task in loader)
