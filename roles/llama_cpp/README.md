# llama_cpp

Deploys the **light serving tier**: [llama.cpp](https://github.com/ggml-org/llama.cpp)
(`llama-server`) on an AMD **ROCm** GPU, inside a privileged LXC (guest `llm-fast`,
RX 6800 = gfx1030). `llama-server` runs directly as the systemd unit, in its
native **router mode**, presenting **one** OpenAI-compatible endpoint over
exactly the models a rendered `--models-preset` (one section per present,
guard-checked model — see `templates/llama-cpp-models.ini.j2`) declares.

llama-swap is retired: no value for a single-model card — llama-server's
own router mode covers swapping.
**Residual**: the router's own admission cap (`max_parallel_requests`,
`roles/llm_router`) is per LiteLLM process, and the pool runs three, so up to
three requests can be admitted against this card's one slot; the fourth and
later collisions queue in llama-server itself, bounded by this rung's
`stream_timeout` override (`llm_router_admission_budget_seconds`).

**Absolute rule:** never serve a model with `param_billions >= 14` on this GPU.
The host has hard-locked repeatedly under large-model GPU loads (VRAM eviction
hard-hangs the host); a converge-time assert in `tasks/main.yml` enforces this,
scoped to `llm_fast_group` only.

## Installation

Ships with the `ansible-proxmox-apps` repo; no external install. The container is
provisioned by `tofu-proxmox` and the GPU device nodes (`/dev/kfd`, `/dev/dri`)
are passed in by `ansible-proxmox` (role `lxc_gpu_features`) — both must be in place
first. Wired into `playbooks/site.yml` against `llm_fast_group` (guests tagged
`llm-fast` in the tofu inventory). Tools come from the repo's Nix dev shell
(`direnv allow`).

Ordering: `tofu-proxmox` (LXC shell) → `ansible-proxmox` (GPU passthrough) →
**this role** (llama.cpp + models) → `llm_router` (LiteLLM front door).

## What it does

- Installs `curl`, `ca-certificates`, `tar`, `gzip`.
- Resolves and installs the **latest** llama.cpp release (the `ubuntu-rocm` asset,
  selected by name pattern so both the build tag and the embedded ROCm version can
  move without a role change), once behind a presence guard.
- Adds the `llama-cpp` service user to whatever groups own the passed-in GPU device
  nodes (resolved at runtime via `stat`), so the server can open `/dev/kfd` +
  `/dev/dri` regardless of how host GIDs map to container group names (same idiom as
  `roles/ollama`).
- Never downloads or writes model weights. Each declared GGUF is checked for
  presence in `llama_cpp_models_dir` (populated out of band from shared model
  storage; the role asserts the mount is read-only); absent ones are reported.
- Renders `llama_cpp_config_file` (a `--models-preset` INI, one section per
  present model — `templates/llama-cpp-models.ini.j2`) and a systemd unit
  running `llama-server` directly in router mode against it, listening on
  `service_ports.llm_fast_api`. Restart-on-failure is applied by the shared
  `systemd_restart_policy` role via `group_vars/llm_fast_group.yml`.

## Models

| model_name | aliases | kind |
| --- | --- | --- |
| `qwen3-4b` | — | chat (`--jinja`) |
| `embeddings` | `nomic-embed-text-v1.5` | `--embeddings` |

`hermes-4-14b` (14B) was removed — see "Absolute rule" above. The preset's
section name (== `model_name`/registry `client_model_id`) is the id the
router serves — unlike a plain `--models-dir` scan, it does not depend on the
GGUF filename, and each section carries its own ctx-size and
chat/embeddings/rerank flags, so mixing model kinds on one guest still gets
the right flags for each.

A `llama_cpp_models` entry can also set `rerank: true` (a reranking model —
`embeddings = true` + `rerank = true` + `pooling = rank`) and `pooling:
<mean|cls|last|none>` (overrides the embeddings branch's default of `mean`
for a model whose own GGUF wants something else, e.g. BAAI/bge-m3's CLS
pooling) — see the field doc in `defaults/main/00-core.yml`. This host
(`llm-fast`) does not use either; the `llm_vllm_group` and `llm_cpu_9b_group`
group_vars do, for the estate's embedding/reranker pair.

## GPU backend

`llama_cpp_gpu_backend` selects which prebuilt upstream binary is deployed.
Nothing is compiled — upstream publishes one Linux asset per backend.

| Value | Build | Devices | Notes |
| --- | --- | --- | --- |
| `rocm` (default) | `ubuntu-rocm` | `/dev/kfd`, `/dev/dri` | `-ngl 99`, `HSA_OVERRIDE_GFX_VERSION=10.3.0` for gfx1030 |
| `vulkan` | `ubuntu-vulkan` | `/dev/nvidia*` | the NVIDIA path; gated on a `vulkaninfo` device check |
| `cpu` | `ubuntu` plain | none | `-ngl 0`, smaller context, all GPU tasks skipped |

`llama_cpp_gpu` remains as a derived boolean (`backend != 'cpu'`) so existing
task and template conditions were not touched.

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
# Deploy (after terraform + ansible-proxmox GPU passthrough)
env -u DOPPLER_PROJECT -u DOPPLER_CONFIG -u DOPPLER_ENVIRONMENT doppler run -- \
  ./scripts/run-ansible.sh playbooks/site.yml --limit llm-fast --tags llama_cpp,ai
```

## Not yet live-validated

Verify on the first converge (W6): (a) the prebuilt ROCm llama.cpp binary runs
against the container's ROCm userspace (it is dynamically linked to the ROCm runtime
— the LXC must provide it; see `llama_cpp_rocm_packages`); (b)
`HSA_OVERRIDE_GFX_VERSION=10.3.0` is honoured by the release build for gfx1030; (c)
the GGUF asset filenames still resolve on HuggingFace.
