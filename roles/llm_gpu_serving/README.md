# llm_gpu_serving

Deploys profile-based GPU inference with a pinned vLLM virtual environment and
the CUDA release binary for llama.cpp. Each enabled profile receives its own
systemd unit; the role keeps only `llm_active_profile` running and serving on
the shared API listener.

## Installation

A selected target supplies a writable local cache and a declared model-origin
mount. The role validates both and does not create a missing mount point.

## What it does

- Installs a Renovate-pinned uv binary and creates a dedicated Python virtual environment.
- Installs the Renovate-pinned `hf` CLI with uv under the supplied model cache; uv's package, Python, and tool caches use that same directory.
- Installs the Renovate-pinned vLLM version with the `b12x` extra for SM120
  kernel support.
- Resolves a recent llama.cpp release and installs its Linux x64 CUDA archive
  using the same release metadata and archive-layout checks as `llama_cpp`.
- Seeds `model_store: true` artifacts from `llm-models.d/65-gpu-pro6000-artifacts.yml`
  into the declared origin with immutable Hub revisions. The seed playbook
  checks for running GPU compute applications before each download and verifies
  every populated repository with Hub checksums.
- Pulls the active profile's registered repository from the origin into the
  writable local cache under `models/`, verifies the pinned revision, and then
  lets the engine use that local path. Campaign playbooks can include the
  `llm_gpu_serving` role with `tasks_from: cache-sync.yml` and an artifact ID
  without rendering or changing service units.
- Renders one systemd unit for each enabled profile. On a profile change, handlers
  stop and disable the other units before starting and enabling the selected
  unit. Existing units for profiles later disabled in defaults are stopped,
  disabled, and removed before the active unit starts.
- Retries `GET /v1/models` until the active profile responds successfully.

## Profiles

`llm_profiles` defines four entries: `small`, `medium-a`, `medium-b`, and
`max`. Runtime serving settings live here; model bytes, Hub repository,
include globs, file format, quantization, engine support, and use are defined
once in `llm-models.d/65-gpu-pro6000-artifacts.yml`. The router profile registry
links to those records by `artifact_id`. Only small and medium-a are enabled
and marked for serving; medium-b and max remain disabled campaign candidates
because their campaign artifacts are GGUF and their retained role profiles use
the vLLM runtime.

Profile entries carry their engine and kernel backends, max model length, max
sequences, GPU memory utilization, automatic tool-choice flag, parser defaults,
and API port. The selected artifact supplies model-specific parsers when
present. Select the active entry with `llm_active_profile`. `medium-b` and `max`
are disabled campaign candidates and are not rendered as vLLM units.

The reusable cache-sync task accepts `llm_gpu_serving_cache_sync_artifact_id`
and resolves its repository, revision, and include globs from the artifact
registry. Its default `pull` mode copies from the origin to the local cache;
`llm_gpu_serving_cache_sync_mode: download` is used by the seed playbook to
populate the origin. Callers that only stage a campaign artifact leave
`llm_gpu_serving_cache_sync_notify_service` unset; the normal role sets it to
`true` so changed local model files restart serving.

## Serving floors

A profile may declare `llm_profiles.<name>.floor`: `concurrency`, `input_len`,
and `output_len` name the measured workload, `min_tok_s_per_agent` and
`max_ttft_p90_ms` the accepted result, and `evidence` names the accepted
measurement. Leave it unset until a measurement is accepted. A declared floor is
never partial, and a registry entry marked `servable: true` must have its
profile's floor; `tasks/assert-profile-floors.yml` fails the role otherwise.

## Key variables

| Variable | Purpose |
| --- | --- |
| `llm_active_profile` | Profile whose unit is enabled and running |
| `llm_profiles` | Per-profile engine and serving settings |
| `llm_gpu_serving_model_cache_mount_path` | Writable local model-cache directory supplied for the target |
| `llm_gpu_serving_model_origin_mount_path` | Declared shared origin mount supplied for the target |
| `llm_gpu_serving_uv_version` | Pinned uv installer version, tracked by Renovate |
| `llm_gpu_serving_huggingface_hub_version` | Pinned Hugging Face CLI package version, tracked by Renovate |
| `llm_gpu_serving_vllm_version` | Pinned vLLM package version, tracked by Renovate |
| `llm_gpu_serving_api_port` | Shared API listener port |
| `llm_profiles.<name>.floor` | Optional measured serving floor (see Serving floors) |

## Proxmox host-interim contract

`playbooks/render-primary-host-unit.yml` renders the profile marked `primary`
as a secret-free contract for the `pve_host_systemd_units` role in
`ansible-proxmox`. The renderer requires the target's existing vLLM executable,
model-cache root, bind address, working directory, and output paths as
run-time inputs. It writes a stopped, disabled unit contract; the PVE runner
must receive the contract explicitly and set its desired state for a gated
start.
