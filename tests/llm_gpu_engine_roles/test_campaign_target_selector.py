"""Contract for the medium vLLM campaign target group and its single-alias check."""

from __future__ import annotations

from test_inventory_contract import REPO_ROOT, _load, _walk


def test_medium_vllm_campaign_selector_uses_the_profile_and_requires_one_alias():
    campaign = _load(REPO_ROOT / "playbooks/llm-model-campaign.yml")
    host_tasks = _load(REPO_ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml")
    add_host = next(
        task["ansible.builtin.add_host"]
        for task in _walk(host_tasks)
        if "ansible.builtin.add_host" in task
    )
    groups = add_host["groups"]
    target_assert = next(
        task["ansible.builtin.assert"]
        for task in campaign[0]["tasks"]
        if task.get("name") == "Require one inventory alias when the medium vLLM campaign selector is used"
    )

    assert "llm_campaign_medium_vllm_target" in groups
    assert "load_tofu_medium_vllm_campaign | bool" in groups
    assert "item.value.llm_gpu_engine_identity | default('') == 'vllm'" in groups
    assert "item.value.started | default(false) | bool" in groups
    loader = _load(REPO_ROOT / "inventory/load_tofu/add_lxc_hosts.yml")
    fact = next(
        task["ansible.builtin.set_fact"]["load_tofu_medium_vllm_campaign"]
        for task in loader
        if "load_tofu_medium_vllm_campaign" in task.get("ansible.builtin.set_fact", {})
    )
    assert "load_tofu_gpu_engine_selector == 'vllm'" in fact
    assert "llm_gpu_active_profiles_by_engine.get('vllm', '') is match('^medium')" in fact
    assert "groups['llm_campaign_medium_vllm_target'] | length == 1" in target_assert["that"][0]
    assert "exactly one inventory" in target_assert["fail_msg"]
    loader_text = (REPO_ROOT / "inventory/load_tofu/add_lxc_hosts.yml").read_text()
    assert "llm_campaign_medium_vllm_target" not in loader_text
