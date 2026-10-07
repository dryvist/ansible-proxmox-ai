# llamacpp_serving

Serves only the `llama_cpp` engine in its own GPU guest.

The shared `nvidia_gpu_guest` role owns NVIDIA userspace, CUDA, uv, and model-cache setup.
This role owns engine installation, profiles, systemd units, health checks, and profile switching within its engine.

This role honors `llm_gpu_serving_paused`; its stress-window sequence is
documented in the [GPU serving role README](../llm_gpu_serving/README.md#stress-window).
