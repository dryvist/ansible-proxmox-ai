# llama_cpp

Deploys the **light serving tier**: [llama.cpp](https://github.com/ggml-org/llama.cpp)
(`llama-server`) behind [llama-swap](https://github.com/mostlygeek/llama-swap) on an
AMD **ROCm** GPU, inside a privileged LXC (guest `llm-fast`, RX 6800 = gfx1030).
llama-swap presents **one** OpenAI-compatible endpoint and keeps the configured
models co-resident, so a small general model + an embeddings model fit in
16 GB at Q4 and answer without reload churn.

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
**this role** (llama.cpp + llama-swap + models) → `llm_router` (LiteLLM front door).

## What it does

- Installs `curl`, `ca-certificates`, `tar`, `gzip`.
- Resolves and installs the **latest** llama.cpp release (the `ubuntu-rocm` asset,
  selected by name pattern so both the build tag and the embedded ROCm version can
  move without a role change) and the **latest** llama-swap release, each once
  behind a presence guard.
- Adds the `llama-cpp` service user to whatever groups own the passed-in GPU device
  nodes (resolved at runtime via `stat`), so the server can open `/dev/kfd` +
  `/dev/dri` regardless of how host GIDs map to container group names (same idiom as
  `roles/ollama`).
- Stages the model GGUFs as local files (downloads only when absent).
- Renders the llama-swap config (a co-resident model group) + a systemd unit,
  listening on `service_ports.llm_fast_api`. Restart-on-failure is applied by the
  shared `systemd_restart_policy` role via `group_vars/llm_fast_group.yml`.

## Models

| model_name | aliases | kind |
| --- | --- | --- |
| `qwen3-4b` | — | chat (`--jinja`) |
| `embeddings` | `nomic-embed-text-v1.5` | `--embeddings` |

Both are members of the `chat` / `embeddings` llama-swap groups (`swap: false`
for `embeddings`), so they stay loaded together. `hermes-4-14b` (14B) was
removed — see "Absolute rule" above.

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

## Key variables (`defaults/main.yml`)

| Var | Default | Purpose |
| --- | --- | --- |
| `llama_cpp_api_port` | `tofu_data.constants.service_ports.llm_fast_api` | llama-swap listen port (no hardcode) |
| `llama_cpp_gpu_backend` | `rocm` | `rocm` \| `vulkan` \| `cpu` — selects the upstream asset |
| `llama_cpp_max_param_billions` | `14` | per-guest ceiling, asserted everywhere; raise explicitly per guest |
| `llama_cpp_vulkan_packages` | loader + `vulkan-tools` | `vulkaninfo` backs the device-visibility gate |
| `llama_cpp_models` | 2-model list | served models + GGUF sources (each needs `param_billions`) |
| `llama_cpp_install_dir` | `/opt/llama-cpp` | binary + bundled ROCm `.so` files (also `LD_LIBRARY_PATH`) |
| `llama_cpp_models_dir` | `/var/lib/llama-cpp/models` | staged GGUFs (persistent volume) |
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
