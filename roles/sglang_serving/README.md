# sglang_serving

Installs a pinned SGLang release in the GPU guest virtual environment and
serves the selected registered profile through a systemd unit.

Profiles configure context length, request concurrency, FP8 KV cache, parser selection, PLE offload, and optional
NEXTN speculative decoding. The active unit is checked through `/v1/models` after activation.
