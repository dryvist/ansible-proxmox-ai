# llama_cpp

Deploys the **light serving tier**: [llama.cpp](https://github.com/ggml-org/llama.cpp)
(`llama-server`) in guests assigned to `llm_fast_group`. The group includes the
CPU guest `llm-light`; `llama_cpp_gpu_backend` selects a ROCm, Vulkan, or CPU
build per guest. `llama-server` runs as a systemd service in native **router
mode**, presenting **one** OpenAI-compatible endpoint over the models declared
in its rendered `--models-preset` (see `templates/llama-cpp-models.ini.j2`).

The router's admission cap (`max_parallel_requests`, `roles/llm_router`) is per
LiteLLM process. llama-server queues requests beyond the configured
`llama_cpp_parallel` value, bounded by the router's
`llm_router_admission_budget_seconds` setting.

**Model-size rule:** models in `llm_fast_group` must be below 14B parameters.
The role also enforces `llama_cpp_max_param_billions` for each guest.

## Installation

The container is provisioned by `tofu-proxmox`. GPU device nodes are passed in
by `ansible-proxmox` (role `lxc_gpu_features`) for GPU builds. The role is wired
into `playbooks/site.yml` against `llm_fast_group` (guests tagged `llm-fast` in
the tofu inventory). Tools come from the repo's Nix dev shell (`direnv allow`).

Ordering: `tofu-proxmox` (LXC shell) → `ansible-proxmox` (GPU passthrough for
GPU builds) → **this role** (llama.cpp + models) → `llm_router` (LiteLLM front door).

## What it does

- Installs `curl`, `ca-certificates`, `tar`, `gzip`.
- Installs the Renovate-pinned llama.cpp asset selected for `rocm`, `vulkan`, or
  `cpu` by the shared `llamacpp_release` contract. The recorded asset triggers
  installation only when that backend's pin changes; downloads use
  published inventory cache endpoints.
- Adds the `llama-cpp` service user to whatever groups own the passed-in GPU device
  nodes (resolved at runtime via `stat`), so the server can open `/dev/kfd` +
  `/dev/dri` regardless of how host GIDs map to container group names (same idiom as
  `roles/ollama`).
- Never downloads or writes model weights. Each declared GGUF must already be
  present in `llama_cpp_models_dir` (populated out of band from shared model
  storage; the role asserts the mount is read-only) — the converge fails
  loudly, naming the missing file, rather than silently serving fewer models
  than declared.
- Renders `llama_cpp_config_file` (a `--models-preset` INI, one section per
  declared model — `templates/llama-cpp-models.ini.j2`) and a systemd unit
  running `llama-server` directly in router mode against it, listening on
  `service_ports.llm_fast_api`. Restart-on-failure is applied by the shared
  `systemd_restart_policy` role via `group_vars/llm_fast_group.yml`.
- After the service is up, loads every declared model once and confirms it is
  still resident afterward (`tasks/verify-residency.yml`) — fails loudly if
  `llama_cpp_models_max` or a host's VRAM/RAM does not actually cover the
  declared set at its configured `ctx_size`.

## Models

| model_name | aliases | kind |
| --- | --- | --- |
| `qwen3-4b` | — | chat (`--jinja`) |
| `embeddings` | `nomic-embed-text-v1.5` | `--embeddings` |

The preset's section name (== `model_name`/registry `client_model_id`) is the id the
router serves — unlike a plain `--models-dir` scan, it does not depend on the
GGUF filename, and each section carries its own ctx-size and
chat/embeddings/rerank flags, so mixing model kinds on one guest still gets
the right flags for each.

A `llama_cpp_models` entry can also set `rerank: true` (a reranking model —
`embeddings = true` + `rerank = true` + `pooling = rank`) and `pooling:
<mean|cls|last|none>` (overrides the embeddings branch's default of `mean`
for a model whose own GGUF wants something else, e.g. BAAI/bge-m3's CLS
pooling) — see the field doc in `defaults/main/00-core.yml`. The role defaults
do not set either field; `llm_vllm_group` and `llm_cpu_9b_group` group_vars use
them for their embedding/reranker entries.

## GPU backend

`llama_cpp_gpu_backend` selects which prebuilt upstream binary is deployed.
Nothing is compiled — upstream publishes one Linux asset per backend.

| Value | Build | Devices | Notes |
| --- | --- | --- | --- |
| `rocm` (default) | `ubuntu-rocm` | `/dev/kfd`, `/dev/dri` | `-ngl 99`, ROCm architecture override |
| `vulkan` | `ubuntu-vulkan` | `/dev/nvidia*` | the NVIDIA path; gated on a `vulkaninfo` device check |
| `cpu` | `ubuntu` plain | none | `-ngl 0`, smaller context, all GPU tasks skipped |

`llama_cpp_gpu` is derived from the backend and gates shared GPU tasks.

**Why Vulkan and not CUDA for NVIDIA.** Upstream ships no Linux CUDA release
binary — the CUDA prebuilts are Windows-only. Vulkan is the Linux GPU backend
upstream actually publishes, and it runs on NVIDIA. The NVIDIA Vulkan ICD
reaches the card through `/dev/nvidia*` and does **not** need `/dev/dri`, which
does not exist in an NVIDIA-passthrough LXC.

**The Vulkan path is gated, not assumed.** If Vulkan cannot see a device,
llama.cpp does not fail — it serves from the CPU, silently, while the unit
reports active and completions return. The role therefore runs `vulkaninfo`
and fails the converge when no device is reported, rather than shipping a
GPU deployment that quietly is not one.

## Key variables (`defaults/main/`)

| Var | Default | Purpose |
| --- | --- | --- |
| `llama_cpp_api_port` | `tofu_data.constants.service_ports.llm_fast_api` | llama-server listen port (no hardcode) |
| `llama_cpp_gpu_backend` | `rocm` | `rocm` \| `vulkan` \| `cpu` — selects the upstream asset |
| `llama_cpp_max_param_billions` | `14` | per-guest ceiling, asserted everywhere; raise explicitly per guest |
| `llama_cpp_vulkan_packages` | loader + `vulkan-tools` | `vulkaninfo` backs the device-visibility gate |
| `llama_cpp_models` | 2-model list | served models + GGUF sources (each needs `param_billions`) |
| `llama_cpp_models_max` | `1` | `--models-max` — resident-model cap for router mode |
| `llama_cpp_parallel` | mandatory, no default | `-np`/`--parallel` — every group sets its own value |
| `llama_cpp_install_dir` | `/opt/llama-cpp` | binary + bundled ROCm `.so` files (also `LD_LIBRARY_PATH`) |
| `llama_cpp_models_dir` | `container_models_mount_path` (tofu-published) | read-only shared model mount for each preset entry's `model =` path |
| `llama_cpp_config_file` | `/etc/llama-cpp/models.ini` | the rendered `--models-preset` |
| `llama_cpp_rocm_packages` | `[]` | best-effort container ROCm runtime packages |

## Usage

```bash
# Deploy the light serving capability group
env -u DOPPLER_PROJECT -u DOPPLER_CONFIG -u DOPPLER_ENVIRONMENT doppler run -- \
  ./scripts/run-ansible.sh playbooks/site.yml --limit llm_fast_group --tags llama_cpp,ai
```
