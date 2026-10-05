# llm_gpu_serving

Deploys profile-based GPU inference with a pinned vLLM virtual environment and
the CUDA release binary for llama.cpp. Each configured profile receives its
own systemd unit; the role keeps only `llm_active_profile` running and serving
on the shared tofu-defined API port.

## Installation

The guest is provisioned by tofu-proxmox. Before converging this role, tofu
must provide the read-only model origin mount and writable local cache mount in
the published container inventory. The role checks both directories and does
not create the mount points.

## What it does

- Installs uv and creates a dedicated Python virtual environment.
- Installs the Renovate-pinned vLLM version with the `b12x` extra for SM120
  kernel support.
- Resolves a recent llama.cpp release and installs its Linux x64 CUDA archive
  using the same release metadata and archive-layout checks as `llama_cpp`.
- Copies only the active profile's model directory from the tofu-provisioned
  origin mount to the local cache with `rsync --archive --delete`.
- Renders one systemd unit for every profile. On a profile change, handlers
  stop and disable the other units before starting and enabling the selected
  unit.
- Retries `GET /v1/models` until the active profile responds successfully.

## Profiles

`llm_profiles` defines four provisional entries: `small`,
`medium-a`, `medium-b`, and `max`. Each profile joins to its model in
`llm-models.d/60-gpu-pro6000.yml`; that registry is the only source for model
ids and served names. The benchmark campaign finalises those registry entries,
quantization, context length, backend and parser selection, memory utilization,
and stream counts before production use.

Each entry carries its engine, quantization and kernel backends, max model
length, max sequences, GPU memory utilization, automatic tool-choice flag,
tool-call and reasoning parsers, API port, and optional llama.cpp GGUF filename.
Select the active entry with `llm_active_profile`.

## Key variables

| Variable | Purpose |
| --- | --- |
| `llm_active_profile` | Profile whose unit is enabled and running |
| `llm_profiles` | Per-profile engine and serving settings |
| `llm_gpu_serving_model_origin_mount_path` | Read-only model source mount from tofu inventory |
| `llm_gpu_serving_model_cache_mount_path` | Writable local model cache mount from tofu inventory |
| `llm_gpu_serving_vllm_version` | Pinned vLLM package version, tracked by Renovate |
| `llm_gpu_serving_api_port` | Shared listener port from `tofu_data.constants.service_ports.llm_fast_api` |
