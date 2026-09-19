# llama_cpp

Deploys the **light serving tier**: [llama.cpp](https://github.com/ggml-org/llama.cpp)
(`llama-server`) on an AMD **ROCm** GPU, inside a privileged LXC (guest `llm-fast`,
RX 6800 = gfx1030). `llama-server` runs directly as the systemd unit, in its
native **router mode**, presenting **one** OpenAI-compatible endpoint over
whatever GGUFs its `--models-dir` finds present on the guest's read-only model
mount.

llama-swap fronted this role until 2026-09-19 and was retired: with one model
per card, its swap feature was never exercisable under the router's ≤15s
admission rule, and its own config surface (a default concurrency cap that
didn't match the server's real slot count, a separate YAML the two had to
agree on) caused two serving faults in one day. llama-server's own router
mode does the same job — model discovery, load/unload, a built-in web UI —
without a second process or a second config format.

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
- Renders a systemd unit running `llama-server` directly in router mode
  (`--models-dir {{ llama_cpp_models_dir }}`), listening on
  `service_ports.llm_fast_api`. Restart-on-failure is applied by the shared
  `systemd_restart_policy` role via `group_vars/llm_fast_group.yml`.

## Models

| model_name | aliases | kind |
| --- | --- | --- |
| `qwen3-4b` | — | chat (`--jinja`) |
| `embeddings` | `nomic-embed-text-v1.5` | `--embeddings` |

`hermes-4-14b` (14B) was removed — see "Absolute rule" above. Router mode
serves whatever GGUFs `--models-dir` finds; the served id is the GGUF
filename with `.gguf` stripped (or the subdirectory name for a
multimodal/multi-shard model), NOT the `model_name` above — see the
`llama_cpp_models` comment in `defaults/main/00-core.yml`. A guest serving
models with different flags (this one mixes a chat model and an embeddings
model) cannot express that per-model under plain `--models-dir` — only
shared CLI defaults apply to every loaded instance; `--models-preset` (an
INI, not used by this role yet) would be needed for real per-model overrides.

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
| `llama_cpp_models_dir` | `/var/lib/llama-cpp/models` | read-only shared model mount; also `--models-dir` |
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
