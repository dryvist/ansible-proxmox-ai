# ollama

Deploys **Ollama** as the local LLM inference server on an AMD **ROCm** GPU,
inside an LXC (CT 167 `hermes-infer`, RX 6800), and provisions **Hermes 4 14B**.

## Installation

Ships with the `ansible-proxmox-apps` repo; no external install. The container is
provisioned by `tofu-proxmox` and the GPU device nodes (`/dev/kfd`,
`/dev/dri`) are passed in by `ansible-proxmox` (role `lxc_gpu_features`) — both
must be in place first. This role is wired into `playbooks/site.yml` against the
`ollama_group` (containers tagged `ollama` in the tofu inventory). Tools come
from the repo's Nix dev shell (`direnv allow`).

Ordering: `tofu-proxmox` (LXC shell) → `ansible-proxmox` (GPU passthrough) →
**this role** (Ollama + ROCm + model) → `open_webui` (chat UI).

## What it does

- Installs `curl`, `ca-certificates`, `zstd` (a minimal LXC template lacks them;
  the install pipe + ROCm tarball overlay both require them).
- Installs Ollama via the official script (creates the `ollama` user + systemd unit).
- Overlays the **ROCm runtime** tarball (the installer ships CPU/NVIDIA libs only).
- Adds `ollama` to whatever groups own the passed-in GPU device nodes (resolved at
  runtime via `stat`), so the service can open `/dev/kfd` + `/dev/dri` regardless of
  how the host GIDs map to container group names.
- Writes a systemd env drop-in (`OLLAMA_HOST`, `OLLAMA_MODELS`,
  `HSA_OVERRIDE_GFX_VERSION=10.3.0` for gfx1030, flash-attention, keep-alive).
- Stages the **Hermes 4 14B** Q4_K_M GGUF as a local file (downloads from
  HuggingFace only when absent) and registers it as `hermes4` via `ollama create`.
- Restart-on-failure is applied by the shared `systemd_restart_policy` role via
  `group_vars/ollama_group.yml`.

## Key variables (`defaults/main.yml`)

| Var | Default | Purpose |
| --- | --- | --- |
| `ollama_api_port` | `tofu_data.constants.service_ports.ollama_api` | API port (no hardcode) |
| `ollama_models_dir` | `/var/lib/ollama` | Model store (120 GB local-zfs vol) |
| `ollama_hsa_override_gfx_version` | `10.3.0` | RX 6800 / Navi 21 / gfx1030 |
| `ollama_model_name` | `hermes4` | Local alias |
| `ollama_gguf_filename` | `NousResearch_Hermes-4-14B-Q4_K_M.gguf` | Staged GGUF blob |
| `ollama_import_dir` | `{{ ollama_models_dir }}/import` | GGUF staging dir (persistent vol) |
| `ollama_gguf_url` | bartowski HF `resolve/main/<file>` | Download source (only if absent) |

## Usage

```bash
# Deploy (after terraform + ansible-proxmox GPU passthrough)
env -u DOPPLER_PROJECT -u DOPPLER_CONFIG -u DOPPLER_ENVIRONMENT doppler run -- \
  ./scripts/run-ansible.sh playbooks/site.yml --limit hermes-infer --tags ollama,ai
```

## Verify

```bash
pct exec 167 -- rocminfo | grep -i gfx            # GPU visible to ROCm
pct exec 167 -- ollama run hermes4 "say hi"        # GPU inference (watch radeontop)
curl http://10.0.40.167:11434/v1/models            # OpenAI-compatible endpoint
```
