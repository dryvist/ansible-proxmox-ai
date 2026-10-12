"""The current GPU guest keeps its profile-switch role until replacement cutover."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVING_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving.yml"
GPU_ENGINE_PLAYBOOK = REPO_ROOT / "playbooks/llm-serving-gpu-engines.yml"


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _legacy_play() -> dict:
    plays = [
        play
        for play in _serving_plays()
        if play.get("hosts", "").split(":&", maxsplit=1)[0] == "llm_gpu_legacy_group"
    ]
    assert len(plays) == 1
    return plays[0]


def _serving_plays() -> list[dict]:
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


def test_legacy_profile_switch_role_targets_only_the_legacy_nvidia_group():
    play = _legacy_play()
    roles = [
        task["ansible.builtin.include_role"]["name"]
        for task in _walk(play["tasks"])
        if "ansible.builtin.include_role" in task
    ]

    assert play["serial"] == 1
    assert play["hosts"] == "llm_gpu_legacy_group"
    assert "llm_gpu_serving" in play["tags"]
    assert roles == ["llm_gpu_serving"]


def test_legacy_and_replacement_engine_plays_precede_the_router_pool():
    hosts = [play.get("hosts", "").split(":&", maxsplit=1)[0] for play in _serving_plays()]
    router = hosts.index("llm_router_group")

    assert hosts.index("llm_gpu_legacy_group") < router
    assert hosts.index("llm_gpu_serving_llama_cpp_group") < router
    assert hosts.index("llm_gpu_serving_vllm_group") < router


def test_legacy_play_does_not_override_the_shared_engine_profile_selector():
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in (SERVING_PLAYBOOK, GPU_ENGINE_PLAYBOOK)
    )

    assert "llm_active_profile" not in _legacy_play().get("vars", {})
    assert "llm_gpu_serving_model_cache_mount_path" not in text
    assert "llm_gpu_serving_model_origin_mount_path" not in text
