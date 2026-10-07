# Sanitized GPU serving target output

Captured on 2026-10-07 with read-only commands in the live GPU guest. These
fixtures record the NVIDIA query, relative model-store listing, and rendered
active serving unit used by the host-contract test. The guest and node
identifiers, absolute model-cache mount root, listener address, serials, and
secret fields are omitted or replaced with variable placeholders.

The sources are `nvidia-smi --query-gpu=name,driver_version,memory.total
--format=csv`, a relative listing of the active model-store repositories and
files, and `systemctl cat llm-gpu-serving-medium-a.service`. The captured unit
uses `LISTEN_ADDRESS` and `{{ llm_gpu_serving_model_cache_path }}` in place of
deployment-specific values.
