# llm_gpu_serving

Legacy combined-engine role. It remains available for the existing guest until
the replacement pair is accepted. New guests use `nvidia_gpu_guest` for shared
NVIDIA userspace and cache setup, plus exactly one of `llamacpp_serving` or
`vllm_serving`; the engine identity and the single Tofu selector determine the
serving group and router profile.

Deploys profile-based GPU inference with a pinned vLLM virtual environment and
the CUDA release binary for llama.cpp. Each enabled profile receives its own
systemd unit; the role keeps only `llm_active_profile` running and serving on
the shared API listener.

## Play

`playbooks/llm-serving.yml` applies this role only to `llm_gpu_legacy_group`,
which the inventory loader populates while no replacement engine pair is
declared. Once the pair exists, the loader selects one engine-specific serving
group and routes `llm_gpu_group` to that same member. `playbooks/site.yml`
imports the serving playbook before the router pool. The active profile is
`llm_active_profile`, derived from the Tofu engine selector and the shared
per-engine profile map; cache and origin paths come from the inventory's
`container_models_*` values. The guest carries no container engine: the
EvalScope LiveCodeBench evaluator runs generated code in a local subprocess
with a per-test timeout unless a recipe requests a Docker sandbox.

## Installation

A selected target supplies a writable local cache and, optionally, a declared
model-origin mount. The role validates the cache and does not create a missing
mount point.

## What it does

- Installs the NVIDIA userspace (`libcuda1`, `nvidia-driver-cuda` for `nvidia-smi`)
  from the vendor apt repository at `llm_gpu_serving_nvidia_userspace_version`, with
  the vendor's version-pinning package and no kernel module or DKMS. The version
  must equal the host driver exactly. The step is skipped when `nvidia-smi`
  already reports that version. Debian guests only.
- Installs a Renovate-pinned uv binary and creates a dedicated Python virtual environment.
- Installs the Renovate-pinned `hf` CLI with uv under the supplied model cache; uv's package, Python, and tool caches use that same directory.
- Links `vllm` and `hf` into `/usr/local/bin` so campaign preflights find them without a venv path.
- Installs the Renovate-pinned vLLM version and the Renovate-pinned `b12x`
  package for SM120 kernel support in one uv command.
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
`max`. All four are enabled so the profile switch can select any of them.
Runtime serving settings live here; model bytes, Hub repository, include
globs, file format, quantization, engine support, GGUF file name, and use are
defined once in `llm-models.d/65-gpu-pro6000-artifacts.yml`. The router profile
registry links to those records by `artifact_id` and stays `enabled: false`,
`servable: false` until a serving floor is measured.

`small` and `medium-a` run vLLM (safetensors). `medium-b` and `max` run
llama.cpp (GGUF) from the installed release binary. A vLLM profile carries its
kernel backends, max model length, max sequences, GPU memory utilization,
automatic tool-choice flag, parser defaults, and API port; the selected
artifact supplies model-specific parsers when present. A llama.cpp profile
carries `max_model_len` (the per-agent context), `max_num_seqs` (parallel
slots), `gpu_layers`, `flash_attention`, and the API port. Its unit passes
`--ctx-size` as `max_model_len` times `max_num_seqs`, `--parallel` as
`max_num_seqs`, the model as `<cache>/<repository>/<gguf_file>`, and `--jinja`
(tool calls are template-driven; llama.cpp has no tool-call parser flag).
Optional `kv_cache_dtype` becomes `--cache-type-k`/`--cache-type-v`,
`reasoning_format` becomes `--reasoning-format`, and `extra_args` is appended
verbatim. Select the active entry with `llm_active_profile`.

The reusable cache-sync task accepts `llm_gpu_serving_cache_sync_artifact_id`
and resolves its repository, revision, and include globs from the artifact
registry. Its default `pull` mode copies from the origin to the local cache;
`llm_gpu_serving_cache_sync_mode: download` is used by the seed playbook to
populate the origin. With no origin declared, either mode downloads the pinned
artifact straight into the local cache and verifies it there. Callers that only stage a campaign artifact leave
`llm_gpu_serving_cache_sync_notify_service` unset; the normal role sets it to
`true` so changed local model files restart serving.

## Stress window

Run `template 76` with `llm_gpu_serving_paused=true`, then `template 18` with
`smoke` or `full`, then `template 76` with `llm_gpu_serving_paused=false`.
The shared pause variable applies to this role and both engine-specific roles.
Pausing stops the selected unit while leaving it enabled, its profile selected,
and its model cache intact. Resuming starts the selected unit and requires the
normal `/v1/models` health check to pass.

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
| `llm_gpu_serving_paused` | Stop serving during a declared stress window; defaults to `false` |
| `llm_active_profile` | Selected profile; it remains selected while its unit is paused |
| `llm_profiles` | Per-profile engine and serving settings |
| `llm_gpu_serving_model_cache_mount_path` | Writable local model-cache directory supplied for the target |
| `llm_gpu_serving_model_origin_mount_path` | Optional shared origin mount; when empty, models download straight into the local cache |
| `llm_gpu_serving_nvidia_userspace_version` | Guest NVIDIA userspace version; equals the host driver version |
| `llm_gpu_serving_uv_version` | Pinned uv installer version, tracked by Renovate |
| `llm_gpu_serving_huggingface_hub_version` | Pinned Hugging Face CLI package version, tracked by Renovate |
| `llm_gpu_serving_vllm_version` | Pinned vLLM package version, tracked by Renovate |
| `llm_gpu_serving_api_port` | Shared API listener port |
| `llm_profiles.<name>.floor` | Optional measured serving floor (see Serving floors) |

## Proxmox host-interim contract

`playbooks/render-primary-host-unit.yml` renders secret-free unit contracts for
the `pve_host_systemd_units` role in `ansible-proxmox`. The renderer requires
the target's existing engine executables, model-cache root, bind address,
working directory, and output paths as run-time inputs: the vLLM executable
(`llm_gpu_serving_host_vllm_bin`) for vLLM profiles and the llama-server
executable (`llm_gpu_serving_host_llamacpp_bin`) for llama.cpp profiles.

`llm_gpu_serving_host_contract_scope` selects the profiles. `primary`
(default) renders the profile marked `primary` to
`llm_gpu_serving_host_contract_unit_path` as a stopped, disabled contract; the
PVE runner sets its desired state for a gated start. `all` renders every
enabled profile to `llm_gpu_serving_host_contract_unit_dir`, one unit each; only
`llm_active_profile` is `started` and enabled, every other profile is `stopped`
and not enabled. Contracts carry `python_virtualenv` and `python_packages`
for vLLM profiles only.
