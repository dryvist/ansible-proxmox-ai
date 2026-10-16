"""Verify SGLang profiles render their registered model and runtime flags."""

from pathlib import Path

import jinja2
import yaml

from test_engine_profiles import _exec_start, _profiles, _registry_profiles

ROOT = Path(__file__).resolve().parents[2]
SGLANG_ROOT = ROOT / "roles/sglang_serving"


def _sglang_profiles() -> dict:
    return yaml.safe_load(
        (SGLANG_ROOT / "defaults/main/10-profiles.yml").read_text(encoding="utf-8")
    )["sglang_serving_profiles"]


def _all_profiles() -> dict:
    return {**_profiles(), **_sglang_profiles()}


def _render(profile_name: str, profile: dict) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(SGLANG_ROOT / "templates"),
        trim_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["comment"] = lambda text: "\n".join(
        f"# {line}" if line else "#" for line in str(text).splitlines()
    )
    model = _registry_profiles()[profile_name]
    resolved = {
        **profile,
        "port": env.from_string(str(profile["port"])).render(nvidia_gpu_guest_api_port=10434),
    }
    common = {
        "ansible_managed": "Managed by Ansible",
        "ansible_facts": {"default_ipv4": {"address": "LISTEN_ADDRESS"}},
        "nvidia_gpu_guest_user": "llm-gpu-serving",
        "nvidia_gpu_guest_group": "llm-gpu-serving",
        "nvidia_gpu_guest_data_dir": "/var/lib/llm-gpu-serving",
        "nvidia_gpu_guest_venv": "/opt/llm-gpu-serving/venv",
        "nvidia_gpu_guest_cuda_home": "/usr/local/cuda-X.Y",
        "nvidia_gpu_guest_hf_home": "HF_CACHE_HOME",
        "nvidia_gpu_guest_listen_host": "LISTEN_ADDRESS",
    }
    return env.get_template("sglang-serving.service.j2").render(
        **common,
        sglang_serving_profile_name=profile_name,
        sglang_serving_profile=resolved,
        sglang_serving_profile_registry_model=model,
        sglang_serving_profile_model_dir=f"/cache/models/{model['artifact']['hf_repo']}",
    )


def test_every_registry_profile_has_exactly_one_engine_profile():
    assert set(_registry_profiles()) == set(_all_profiles())


def test_sglang_profiles_render_the_requested_runtime_flags():
    profiles = _sglang_profiles()
    registry_profiles = _registry_profiles()
    assert len(profiles) == 12

    for model in ("qwen38", "flash-next"):
        for concurrency in (1, 4, 8):
            for mtp in (False, True):
                suffix = "-fp8-mtp" if mtp else "-fp8"
                name = f"{model}-64k-{concurrency}{suffix}"
                profile = profiles[name]
                registry_model = registry_profiles[name]
                exec_start = _exec_start(_render(name, profile))

                assert profile["engine"] == "sglang"
                assert profile["max_model_len"] == 65536
                assert profile["max_num_seqs"] == concurrency
                assert profile["mem_fraction_static"] == (0.96 if model == "flash-next" and mtp else 0.93)
                assert profile["kv_cache_dtype"] == "fp8_e4m3"
                assert profile["mtp"] is mtp
                assert (profile.get("ple_offload_embedding", False)) is (model == "flash-next")
                assert profile["engine"] in registry_model["artifact"]["engines"]
                assert "-m sglang.launch_server" in exec_start
                assert f"--model-path /cache/models/{registry_model['artifact']['hf_repo']}" in exec_start
                assert f"--served-model-name {registry_model['upstream_model_id']}" in exec_start
                assert f"--context-length {profile['max_model_len']}" in exec_start
                assert f"--max-running-requests {concurrency}" in exec_start
                assert "--kv-cache-dtype fp8_e4m3" in exec_start
                assert "--reasoning-parser auto" in exec_start
                assert "--tool-call-parser auto" in exec_start
                assert "--enable-metrics" in exec_start
                assert "--enable-request-time-stats-logging" in exec_start
                assert ("--ple-offload-embedding" in exec_start) is (model == "flash-next")
                assert ("--speculative-algorithm NEXTN" in exec_start) is mtp
                if mtp:
                    assert "--speculative-num-steps 3" in exec_start
                    assert "--speculative-eagle-topk 1" in exec_start
                    assert "--speculative-num-draft-tokens 4" in exec_start
                assert "{{" not in exec_start
