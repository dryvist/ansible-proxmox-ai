"""Check that optional vLLM runtime settings render as their CLI flags."""

from __future__ import annotations

import json

from test_engine_profiles import _exec_start, _profiles, _render


def test_vllm_profiles_render_optional_runtime_flags():
    for name, profile in _profiles().items():
        if profile["engine"] != "vllm":
            continue
        exec_start = _exec_start(_render(name, profile))
        if "quantization" in profile:
            assert f"--quantization {profile['quantization']}" in exec_start
        if "speculative_config" in profile:
            assert f"--speculative-config '{json.dumps(profile['speculative_config'])}'" in exec_start
        if "engram_config" in profile:
            assert f"--engram-config '{json.dumps(profile['engram_config'])}'" in exec_start
        if profile.get("enable_flashinfer_autotune") is False:
            assert "--no-enable-flashinfer-autotune" in exec_start
