"""Selector-driven serving and router inventory projection contracts."""

from __future__ import annotations

from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVING_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving.yml"
GPU_ENGINE_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-gpu-engines.yml"
SITE_PLAYBOOK = REPO_ROOT / "playbooks/site.yml"
ROUTER_GPU_PROFILE_VARS = REPO_ROOT / "inventory/group_vars/all.yml"
ROUTER_DEFAULTS = REPO_ROOT / "roles/llm_router/defaults/main/20-registry.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _serving_plays():
    plays = []
    for play in _load(SERVING_PLAYBOOK):
        imported = play.get("import_playbook", play.get("ansible.builtin.import_playbook"))
        if imported == GPU_ENGINE_PLAYBOOK.name:
            plays.extend(_load(GPU_ENGINE_PLAYBOOK))
        else:
            plays.append(play)
    return plays


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_site_reaches_the_serving_playbook_without_a_tag_filter():
    imports = [play for play in _load(SITE_PLAYBOOK) if play.get("import_playbook") == "llm-serving.yml"]

    assert len(imports) == 1
    assert "tags" not in imports[0]


def test_each_engine_profile_and_router_alias_follow_the_single_tofu_selector():
    all_vars = _load(REPO_ROOT / "inventory/group_vars/all.yml")
    assert all_vars["llm_gpu_active_profiles_by_engine"] == {
        "llama_cpp": "medium-b",
        "vllm": "medium-a",
    }

    template = jinja2.Environment().from_string(all_vars["llm_active_profile"])

    def profile(engine: str | None, pair_present: bool) -> str:
        data = {} if engine is None else {"llm_gpu_engine": engine}
        hostvars = {"localhost": {"tofu_data": data}}
        groups = {
            "llm_gpu_engine_llama_cpp_group": ["llama-guest"] if pair_present else [],
            "llm_gpu_engine_vllm_group": ["vllm-guest"] if pair_present else [],
        }
        return template.render(
            hostvars=hostvars,
            groups=groups,
            llm_gpu_active_profiles_by_engine=all_vars["llm_gpu_active_profiles_by_engine"],
        )

    assert profile("llama_cpp", True) == "medium-b"
    assert profile("vllm", True) == "medium-a"
    # Until both replacement guests exist, preserve the current legacy profile.
    assert profile("llama_cpp", False) == "medium-a"
    assert profile(None, False) == "medium-a"


def test_inventory_loader_assigns_selected_router_membership_and_retains_both_engine_identities():
    loader = _load(REPO_ROOT / "inventory/load_tofu/add_lxc_hosts.yml")
    host_tasks = _load(REPO_ROOT / "inventory/load_tofu/add_lxc_host_inventory.yml")
    add_host = next(task["ansible.builtin.add_host"] for task in _walk(host_tasks) if "ansible.builtin.add_host" in task)
    groups = add_host["groups"]

    assert "load_tofu_gpu_engine_pair_present" in groups
    assert "load_tofu_gpu_engine_selector == 'llama_cpp'" in groups
    assert "load_tofu_gpu_engine_selector == 'vllm'" in groups
    assert "llm_gpu_engine_llama_cpp_group" in groups
    assert "llm_gpu_engine_vllm_group" in groups
    assert "llm_gpu_legacy_group" in groups
    assert "value.llm_gpu_engine_identity" in yaml.safe_dump(loader + host_tasks)
    selector = next(task for task in loader if task.get("name") == "Resolve the selector and declared GPU engine members")
    members = selector["ansible.builtin.set_fact"]["load_tofu_gpu_engine_members"]
    assert "nvidia_gpu_group" in groups
    assert "'nvidia-gpu'" in groups
    assert "selectattr('value.llm_gpu_engine_identity', 'defined')" in members
    assert "selectattr('value.llm_gpu_engine_identity', 'in', ['llama_cpp', 'vllm'])" in members
    assert "llm_gpu_group" in groups


def test_every_gpu_serving_play_is_engine_specific_and_before_the_router():
    plays = _serving_plays()
    hosts = [play.get("hosts") for play in plays]
    router = hosts.index("llm_router_group")
    gpu_plays = [
        play
        for play in plays
        if play.get("hosts") in {
            "llm_gpu_legacy_group:&nvidia_gpu_group",
            "llm_gpu_serving_llama_cpp_group:&nvidia_gpu_group",
            "llm_gpu_serving_vllm_group:&nvidia_gpu_group",
        }
    ]

    assert len(gpu_plays) == 3
    assert all(hosts.index(play["hosts"]) < router for play in gpu_plays)
    assert all("docker_engine" not in yaml.safe_dump(play) for play in gpu_plays)

    for play in gpu_plays:
        if play["hosts"] == "llm_gpu_legacy_group:&nvidia_gpu_group":
            continue
        assert play["any_errors_fatal"] is True
        mark_failed = play["tasks"][0]["rescue"][-1]
        assert any(task.get("ansible.builtin.include_tasks") == "tasks/record_isolated_failure.yml" for task in play["tasks"][0]["rescue"])
        assert mark_failed["ansible.builtin.set_fact"]["llm_gpu_engine_convergence_failed"] is True

    router_play = next(play for play in plays if play.get("hosts") == "llm_router_group")
    router_pre_tasks = router_play["pre_tasks"]
    assert router_play["serial"] == 1
    assert router_play["max_fail_percentage"] == 0
    gate = next(
        index for index, task in enumerate(router_pre_tasks)
        if task.get("name") == "Hold router projection until the selected GPU engine is healthy"
    )
    assert any(task.get("name") == "Log the GPU serving health decision before router projection" for task in router_pre_tasks[:gate])
    assert gate >= 2


def test_router_projects_gpu_profiles_only_when_the_selected_host_group_has_a_member():
    expression = _load(ROUTER_GPU_PROFILE_VARS)["llm_router_gpu_profiles_enabled"]
    env = jinja2.Environment()

    def render(groups: dict) -> str:
        return env.from_string(expression).render(groups=groups)

    assert render({}) == "False"
    assert render({"llm_gpu_group": []}) == "False"
    assert render({"llm_gpu_group": ["selected-engine-guest"]}) == "True"
    assert _load(ROUTER_DEFAULTS)["llm_router_gpu_profiles_enabled"] is False
