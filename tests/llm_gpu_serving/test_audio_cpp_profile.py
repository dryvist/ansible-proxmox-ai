"""The Ada small lane selects MiMo and resolves both speech GGUFs."""

from __future__ import annotations

import ast
from pathlib import Path

import jinja2
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(path: str):
    return yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))


def _artifacts() -> list[dict]:
    paths = sorted((REPO_ROOT / "llm-models.d").glob("*artifacts*.yml"))
    return [
        artifact
        for path in paths
        for key, entries in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_model_artifacts") or key.endswith("_artifacts")
        for artifact in entries
    ]


def _registry_entries() -> list[dict]:
    paths = [REPO_ROOT / f"llm-models.d/{name}" for name in ("60-gpu.yml", "61-gpu-sweeps.yml", "62-gpu-matrix.yml")]
    return [
        entry
        for path in paths
        for key, entries in yaml.safe_load(path.read_text(encoding="utf-8")).items()
        if key.startswith("_llm_registry_gpu")
        for entry in entries
    ]


def _combine(left: dict, right: dict, recursive: bool = False) -> dict:
    combined = dict(left)
    for key, value in right.items():
        if recursive and isinstance(combined.get(key), dict) and isinstance(value, dict):
            combined[key] = _combine(combined[key], value, recursive=True)
        else:
            combined[key] = value
    return combined


def _jinja() -> jinja2.Environment:
    env = jinja2.Environment()
    env.filters["combine"] = _combine
    return env


def _loaded_profiles() -> dict:
    tasks = _load("roles/llm_gpu_serving/tasks/load-registry.yml")
    index = next(task for task in tasks if task.get("name") == "Index serving profiles with their model artifacts")
    profiles = _load("roles/llm_gpu_serving/defaults/main/10-profiles.yml")["llm_profiles"]
    host = _load("inventory/host_vars/llm-ada-lovelace-6X-16GB-embed.yml")
    profiles = _combine(profiles, host["llm_gpu_serving_profile_overrides"], recursive=True)
    artifacts = _artifacts()
    artifact_ids = {item["artifact_id"]: item for item in artifacts}
    registry = [
        item for item in _registry_entries()
        if item["profile"] in {"small", "speech"}
    ]
    result = _jinja().from_string(
        index["ansible.builtin.set_fact"]["llm_gpu_serving_registry_by_profile"]
    ).render(
        llm_gpu_serving_registry_profiles=registry,
        llm_gpu_serving_profiles=profiles,
        llm_gpu_serving_profile_artifact_overrides=host["llm_gpu_serving_profile_artifact_overrides"],
        llm_gpu_serving_artifacts_by_id=artifact_ids,
        llm_gpu_serving_model_cache_path="/var/lib/llm/models",
    )
    resolved = ast.literal_eval(result)
    return _combine(profiles, resolved, recursive=True)


def test_ada_small_profile_selects_the_pinned_mimo_gguf():
    host = _load("inventory/host_vars/llm-ada-lovelace-6X-16GB-embed.yml")
    profiles = _loaded_profiles()
    small = profiles["small"]
    artifact = small["artifact"]

    assert host["llm_active_profile"] == "small"
    assert host["llm_router_gpu_member"] is False
    assert host["llm_gpu_serving_profile_overrides"]["small"] == {
        "max_model_len": 4096,
        "max_num_seqs": 2,
    }
    assert host["llm_gpu_serving_profile_artifact_overrides"]["small"] == "mimo-9b-q4-k-m"
    assert (small["max_model_len"], small["max_num_seqs"]) == (4096, 2)
    assert artifact["artifact_id"] == "mimo-9b-q4-k-m"
    assert artifact["revision"] == "4371da10c84fb26da3592d4cf312d24aa82b7b65"
    assert artifact["gguf_file"] == "MiMo-V2.6-Distill-Qwen-9B-Q4_K_M.gguf"
    assert artifact["sha256"] == "4bca6f18c73f72270c7a20c2ea2bea581de8246e318714277120369d34048c81"
    assert artifact["model_store_size_bytes"] == 5841049120
    assert artifact["serving_profile"] == "small"
    assert artifact["engines"] == ["llama_cpp"]


def test_speech_profile_aggregates_the_two_pinned_audio_cpp_models():
    host = _load("inventory/host_vars/llm-ada-lovelace-6X-16GB-embed.yml")
    profile = _loaded_profiles()["speech"]
    models = profile["audio_cpp_models"]
    speech_record = next(item for item in _registry_entries() if item["profile"] == "speech")
    artifacts_by_id = {item["artifact_id"]: item for item in _artifacts()}
    # audio.cpp serves the upstream id the router sends: each route's artifact hf_repo.
    expected_model_ids = {
        artifacts_by_id[route["artifact_id"]]["hf_repo"]
        for route in [speech_record, *speech_record.get("co_resident_routes", [])]
    }

    assert host["llm_gpu_serving_co_resident_profiles"] == ["speech"]
    assert profile["engine"] == "audio_cpp"
    assert {model["id"] for model in models} == expected_model_ids
    assert {model["family"] for model in models} == {"qwen3_asr", "ming3_tts"}
    assert {model["task"] for model in models} == {"asr", "tts"}
    assert {
        model["path"] for model in models
    } == {
        "/var/lib/llm/models/audio-cpp/audio.cpp-gguf/Qwen3-ASR-1.7B-GGUF/qwen3-asr-1.7b-q8_0.gguf",
        "/var/lib/llm/models/audio-cpp/Ming-3.0-TTS-0.5B-GGUF/ming-3.0-tts-0.5b-q8_0.gguf",
    }
    artifacts = {item["artifact_id"]: item for item in _artifacts()}
    assert artifacts["qwen3-asr-1-7b-q8-0"]["sha256"] == "3bc2462eca3a3a8f56abcd860d0943ffdc0546547dde55a9d08589607d6f3936"
    assert artifacts["qwen3-asr-1-7b-q8-0"]["model_store_size_bytes"] == 2473017600
    assert artifacts["qwen3-asr-1-7b-q8-0"]["required_artifact_ids"] == ["ming-3-0-tts-0-5b-q8-0"]
    assert artifacts["ming-3-0-tts-0-5b-q8-0"]["sha256"] == "fcf2aa25e8e9cf2065a936e506581735c64d6255fb7869f09fbb61a2dde09aa1"
    assert artifacts["ming-3-0-tts-0-5b-q8-0"]["model_store_size_bytes"] == 1742280896


def test_unknown_gpu_engine_is_rejected_by_profile_validation():
    tasks = _load("roles/llm_gpu_serving/tasks/validate-profiles.yml")
    task = next(task for task in tasks if task.get("name") == "Validate every GPU serving profile")
    engine_check = task["ansible.builtin.assert"]["that"][0]
    rendered = _jinja().from_string("{{ " + engine_check + " }}").render(
        item={"value": {"engine": "unknown_engine"}}
    )

    assert rendered == "False"


def test_nvidia_llama_cpp_play_converges_co_resident_speech_only_when_selected():
    plays = _load("playbooks/llm-serving-llama-cpp.yml")
    play = next(item for item in plays if item["hosts"] == "llm_vllm_group")
    task = next(item for item in play["tasks"] if "co-resident" in item["name"])
    includes = [step["ansible.builtin.include_role"] for step in task["block"] if "ansible.builtin.include_role" in step]

    assert task["when"] == "llm_gpu_serving_co_resident_profiles | default([]) | length > 0"
    assert includes == [{"name": "llm_gpu_serving", "tasks_from": "co-resident.yml"}]
    assert task["rescue"][0]["vars"]["_isolated_play_name"] == "llm_gpu_serving_co_resident"


def test_co_resident_entry_point_never_touches_the_llama_cpp_server():
    tasks = _load("roles/llm_gpu_serving/tasks/co-resident.yml")
    cache_sync = next(task for task in tasks if "cache-sync-profile-artifacts.yml" in str(task))

    wrapper = _load("roles/llm_gpu_serving/tasks/cache-sync-profile-artifacts.yml")[0]
    notify = _jinja().from_string(wrapper["vars"]["llm_gpu_serving_cache_sync_notify_service"])
    active_unit = next(task for task in tasks if task.get("name", "").startswith("Keep this role's unit"))

    assert not any("notify" in task for task in tasks)
    # The wrapper's own include vars win over the caller's, so the caller's flag must reach it.
    assert cache_sync["vars"]["llm_gpu_serving_cache_sync_profile_notify"] is False
    assert notify.render(llm_gpu_serving_cache_sync_profile_notify=False) == "False"
    assert notify.render() == "True"
    assert active_unit["ansible.builtin.systemd"]["state"] == "stopped"
    assert active_unit["ansible.builtin.systemd"]["enabled"] is False
    assert active_unit["ansible.builtin.systemd"]["name"].endswith("-{{ llm_active_profile }}.service")
    assert all(
        task["loop"] == "{{ llm_gpu_serving_co_resident_profiles }}"
        for task in tasks
        if "loop" in task and "ansible.builtin.stat" not in task
    )


def test_co_resident_path_grants_the_serving_user_nvidia_device_access_and_restarts_on_change():
    tasks = _load("roles/llm_gpu_serving/tasks/co-resident.yml")
    nodes = next(task for task in tasks if "ansible.builtin.stat" in task)
    groups = next(task for task in tasks if "ansible.builtin.user" in task)
    start = next(task for task in tasks if task.get("name", "").startswith("Start each co-resident unit"))
    render = _jinja().from_string(groups["ansible.builtin.user"]["groups"])
    results = [
        {"stat": {"exists": True, "gr_name": "video"}},
        {"stat": {"exists": True, "gr_name": "root"}},
        {"stat": {"exists": False}},
    ]

    assert "/dev/nvidiactl" in nodes["loop"] and "/dev/nvidia-uvm" in nodes["loop"]
    assert groups["ansible.builtin.user"]["append"] is True
    assert render.render(llm_gpu_serving_co_resident_gpu_nodes={"results": results}).strip() == "['video']"
    assert "llm_gpu_serving_co_resident_gpu_groups is changed" in start["ansible.builtin.systemd"]["state"]


def test_co_resident_health_check_synthesizes_speech_on_audio_cpp_profiles():
    block = _load("roles/llm_gpu_serving/tasks/health-check-co-resident.yml")[0]
    synth = next(task for task in block["block"] if task.get("name", "").startswith("Synthesize"))
    pick = _jinja().from_string(synth["vars"]["llm_gpu_serving_co_resident_tts_models"])
    registry = {"speech": {"audio_cpp_models": [{"id": "asr-repo", "task": "asr"}, {"id": "tts-repo", "task": "tts"}]}}

    assert synth["ansible.builtin.uri"]["url"].strip().endswith("'/v1/audio/speech' }}")
    assert synth["ansible.builtin.uri"]["status_code"] == 200
    assert pick.render(item="speech", llm_gpu_serving_registry_by_profile=registry).strip() == "['tts-repo']"
    assert "llm_gpu_serving_profiles[item].engine == 'audio_cpp'" in synth["when"]


def test_co_resident_health_failure_reads_each_unit_status_and_journal():
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.filter.mathstuff import FilterModule as MathFilters

    block = _load("roles/llm_gpu_serving/tasks/health-check-co-resident.yml")[0]
    read = block["rescue"][0]
    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.filters.update(MathFilters().filters())
    items = ast.literal_eval(
        env.from_string(read["loop"]).render(
            llm_gpu_serving_co_resident_profiles=["speech"], llm_gpu_serving_health_journal_lines=80
        )
    )
    argvs = [
        env.from_string(read["ansible.builtin.command"]["argv"]).render(
            item=item, llm_gpu_serving_unit_prefix="llm-gpu-serving"
        )
        for item in items
    ]

    assert argvs == [
        "['systemctl', 'status', 'llm-gpu-serving-speech.service', '--no-pager']",
        "['journalctl', '-u', 'llm-gpu-serving-speech.service', '--no-pager', '-n', '80']",
    ]
    assert block["rescue"][-1]["ansible.builtin.fail"]


def test_audio_cpp_engine_installs_the_nccl_runtime_its_server_links():
    # The release's only Linux CUDA server build links libnccl.so.2; its archives do not ship it.
    tasks = _load("roles/llm_gpu_serving/tasks/inspect-install-engine-assets.yml")
    nccl = next(task for task in tasks if task.get("ansible.builtin.apt", {}).get("name") == "libnccl2")
    defaults = _load("roles/llm_gpu_serving/defaults/main/00-core.yml")

    assert "'audio_cpp' in llm_gpu_serving_selected_engines" in nccl["when"]
    assert nccl["ansible.builtin.apt"]["state"] == "present"
    assert "cuda12.8-colab" in defaults["llm_gpu_serving_audio_cpp_server_asset_name"]


def test_ada_speech_listens_on_the_llama_cpp_port_plus_one():
    # The router derives speech_port from the same key, plus one.
    host = _load("inventory/host_vars/llm-ada-lovelace-6X-16GB-embed.yml")
    core = _load("roles/llm_gpu_serving/defaults/main/00-core.yml")

    assert host["llm_gpu_serving_api_port_key"] == "llm_fast_api"
    assert core["llm_gpu_serving_co_resident_api_port"] == "{{ llm_gpu_serving_api_port | int + 1 }}"
