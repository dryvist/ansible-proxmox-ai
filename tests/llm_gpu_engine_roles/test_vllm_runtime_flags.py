"""Check that optional vLLM runtime settings render as their CLI flags."""

from __future__ import annotations

import json

from test_engine_profiles import _exec_start, _profiles, _render


def test_vllm_profiles_render_optional_runtime_flags():
    for name, profile in _profiles().items():
        if profile["engine"] != "vllm" or not profile.get("enabled", True):
            continue
        exec_start = _exec_start(_render(name, profile))
        if "quantization" in profile:
            assert f"--quantization {profile['quantization']}" in exec_start
        if "speculative_config" in profile:
            rendered = json.loads(exec_start.split("--speculative-config '", 1)[1].split("'", 1)[0])
            assert all(rendered.get(key) == value for key, value in profile["speculative_config"].items())
        if "engram_config" in profile:
            assert f"--engram-config '{json.dumps(profile['engram_config'])}'" in exec_start
        if profile.get("enable_flashinfer_autotune") is False:
            assert "--no-enable-flashinfer-autotune" in exec_start


def test_high_concurrency_qwen_profile_renders_capacity_arguments():
    profile = _profiles()["qwen38-96k-11-fp8-mtp"]
    exec_start = _exec_start(_render("qwen38-96k-11-fp8-mtp", profile))

    assert "--scheduling-policy priority" in exec_start
    assert "--max-model-len 98304" in exec_start
    assert "--max-num-seqs 16" in exec_start
    assert "--max-num-batched-tokens 8192" in exec_start
    assert "--enable-chunked-prefill" in exec_start
    assert "--gpu-memory-utilization 0.75" in exec_start
    assert "--kv-cache-dtype fp8" in exec_start
    assert "--quantization modelopt" in exec_start
    rendered = json.loads(exec_start.split("--speculative-config '", 1)[1].split("'", 1)[0])
    assert rendered == {"method": "mtp", "num_speculative_tokens": 3}
