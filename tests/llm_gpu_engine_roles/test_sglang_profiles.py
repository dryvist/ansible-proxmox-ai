"""Verify SGLang profiles render their registered model and runtime flags."""

from test_engine_profiles import _exec_start, _registry_profiles, _render, _sglang_profiles


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
