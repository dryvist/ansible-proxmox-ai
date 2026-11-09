# vllm_serving

Serves only the `vllm` engine in its own GPU guest.

The shared `nvidia_gpu_guest` role owns NVIDIA userspace, CUDA, uv, and model-cache setup.
This role owns engine installation, profiles, systemd units, health checks, and profile switching within its engine.

This role honors `llm_gpu_serving_paused`; its stress-window sequence is
documented in the [GPU serving role README](../llm_gpu_serving/README.md#stress-window).

## High-concurrency Qwen profile

The `qwen38-96k-11-fp8-mtp` profile defaults to a 98,304-token engine window, 16 sequences,
8,192 batched tokens, chunked prefill enabled, 0.75 GPU memory utilization, FP8 KV cache,
ModelOpt quantization, and three speculative MTP tokens. The profile's memory-screen defaults
are 2,048, 8,192, and 32,768 input tokens with 512 output tokens per sequence. These values
are role defaults in `defaults/main/10-profiles.yml` and can be overridden through
`vllm_serving_profiles`.

`max_num_batched_tokens` is omitted from other profiles unless set. Chunked prefill is omitted
unless enabled. The `medium-a` profile retains its 262,144-token engine window.
