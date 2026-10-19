# llm_gpu_serving

Combined-engine role for GPU guests that are not members of a declared
replacement engine set. When the set is declared, its guests use
`nvidia_gpu_guest` for shared NVIDIA userspace and cache setup, plus exactly one
of `llamacpp_serving`, `vllm_serving`, or `sglang_serving`; the engine identity
and Tofu selector determine the serving group and router profile.

Deploys profile-based GPU inference with vLLM packages for vLLM profiles and a
prebuilt CUDA release binary for llama.cpp profiles. Each enabled profile
receives its own systemd unit; the role keeps only `llm_active_profile` running
and serving on the shared API listener.

## Play

`playbooks/llm-serving.yml` applies this role only to `llm_gpu_legacy_group`,
which the inventory loader populates from guests tagged `llm-gpu` (also added
to `llm_gpu_group`) or `llm-gpu-small` while no replacement engine set is
declared. Once the set exists, the loader selects one engine-specific serving
group and routes `llm_gpu_group` to that same member. `playbooks/site.yml`
imports the serving playbook before the router pool. `llm_active_profile`
defaults through the Tofu selector and shared profile map in
`inventory/group_vars/all.yml`; a host variable in
`inventory/host_vars/<inventory_hostname>.yml` overrides that selection. The
small guest selects `small` there, while the unoverridden medium guest keeps the
`medium-a` fallback. Cache and origin paths come from the inventory's
`container_models_*` values. The guest carries no container engine: the
EvalScope LiveCodeBench evaluator runs generated code in a local subprocess
with a per-test timeout unless a recipe requests a Docker sandbox.

## Installation

A selected target supplies a writable local cache and, optionally, a declared
model-origin mount. The role validates the cache and does not create a missing
mount point. Package, Python, Hugging Face, CUDA repository, and release
downloads use the published inventory's `cache_proxy_urls.apt_cache` list; a
missing list blocks the converge before upstream fetches.

```sh
ansible-galaxy install -r requirements.yml --force
```

## Usage

The legacy group's serving play runs this role, one guest at a time. A scoped
converge names the group and `localhost`, as the inventory loader needs:

```sh
scripts/run-ansible.sh playbooks/llm-serving-gpu-engines.yml -i inventory/hosts.yml \
  --tags llm_gpu_serving --limit llm_gpu_legacy_group,localhost
```

## What it does

- Installs NVIDIA userspace (`nvidia-vulkan-icd`, `libcuda1`, and
  `nvidia-driver-cuda` for `nvidia-smi`) from the vendor apt repository, pinned
  to the host module version read from `/proc/driver/nvidia/version`. No kernel
  module or DKMS is installed. Debian guests only.
- Installs a Renovate-pinned uv binary and creates the vLLM virtual environment only when the active profile uses vLLM.
- Installs the Renovate-pinned `hf` CLI with uv under the supplied model cache; uv's package, Python, and tool caches use that same directory.
- Installs the CUDA toolkit, C compiler and development headers only when the active profile uses vLLM.
- Links `vllm` into `/usr/local/bin` for vLLM profiles and always links `hf` there for campaign preflights.
- Installs the Renovate-pinned vLLM version and the Renovate-pinned `b12x`
  package for SM120 kernel support in one uv command for vLLM profiles.
- Installs the Renovate-pinned llama.cpp CUDA assets from the shared
  `llamacpp_release` contract only when the recorded asset changes.
- Seeds `model_store: true` artifacts from the model registry into the declared
  origin with immutable Hub revisions. The GLM shard is not seeded. The seed playbook
  checks for running GPU compute applications before each download and verifies
  every populated repository with Hub checksums.
- Pulls the active profile's registered repository from the origin into the
  writable local cache under `models/`, verifies the pinned revision, and then
  lets the engine use that local path. Campaign playbooks include the
  `nvidia_gpu_guest` role with `tasks_from: cache-sync.yml` and an artifact ID
  without rendering or changing service units.
- Renders one systemd unit for each enabled profile. On a profile change, handlers
  stop and disable the other units before starting and enabling the selected
  unit. Existing units for profiles later disabled in defaults are stopped,
  disabled, and removed before the active unit starts.
- Retries `GET /v1/models` until the active profile responds successfully.

## Profiles

The original `llm_profiles` entries are `small`, `medium-a`, `medium-b`, `max`,
and `glm-flash`. `glm-flash` is a separate llama.cpp profile for the pinned
GLM-5.3-Flash UD-IQ1_S artifact; its router entry remains disabled until it is
validated.
Runtime serving settings live here; model bytes, Hub repository, include
globs, file format, quantization, engine support, GGUF file name, and use are
defined once in `llm-models.d/65-gpu-artifacts.yml`,
`llm-models.d/66-gpu-artifacts-glm53flash.yml`, and
`llm-models.d/67-gpu-artifacts-nvfp4-sweep.yml` and
`llm-models.d/69-gpu-artifacts-matrix.yml`. The router profile
registry links to those records by `artifact_id` and stays `enabled: false`,
`servable: false` until a serving floor is measured. The shared GPU registry
also contains the `16gb` llama.cpp candidate owned by `llamacpp_serving`; this
legacy role indexes only its own five profile names.

`small`, `medium-b`, `max`, and `glm-flash` run llama.cpp (GGUF) from the
installed release binary; `medium-a` runs vLLM (safetensors). A vLLM
profile carries its kernel backends, max model length, max sequences, GPU memory utilization,
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

### MoE CPU expert-offload profiles

The four `moe1-*` profiles serve pinned text GGUFs with `--n-cpu-moe`, q8_0 KV,
flash attention, a 1,024 MiB prompt cache, and 66,560 tokens per slot. The
context covers a 65,536-token prompt and 1,024 generated tokens. Their model
registry entries remain disabled until the measured serving floor is accepted.

The CPU expert bytes below come from each pinned GGUF tensor index. GPU-resident
bytes equal the GGUF file size minus those CPU expert tensors.

| Profile | CPU layers N | Slots | CPU expert bytes | GPU-resident bytes |
| --- | ---: | ---: | ---: | ---: |
| `moe1-qwen36-n14-s1` | 14 | 1 | 6,811,549,696 | 15,322,979,296 |
| `moe1-qwen36-n16-s2` | 16 | 2 | 7,784,628,224 | 14,349,900,768 |
| `moe1-bolt-n38-s1` | 38 | 1 | 6,241,517,568 | 15,776,808,960 |
| `moe1-bolt-n40-s2` | 40 | 2 | 6,639,452,160 | 15,378,874,368 |

The selected layer counts leave at least about 1 GiB beyond q8_0 KV for compute
buffers at the configured slot count. The profile-fit guard checks each split
against available GPU memory and leaves at least 1.5 GiB of guest RAM beyond the
CPU expert weights.

The reusable cache-sync task accepts `llm_gpu_serving_cache_sync_artifact_id`
and resolves its repository, revision, and include globs from the artifact
registry. Its default `pull` mode copies from the origin to the local cache;
`llm_gpu_serving_cache_sync_mode: download` is used by the seed playbook to
populate the origin. With no origin declared, either mode downloads the pinned
artifact straight into the local cache and verifies it there. Callers that only stage a campaign artifact leave
`llm_gpu_serving_cache_sync_notify_service` unset; the normal role sets it to
`true` so changed local model files restart serving.

### Co-resident small GPU profile

The legacy GPU serving role can run a second llama.cpp profile beside the main
vLLM profile. Set `llm_gpu_serving_co_resident_profiles: [small-c4-64k]` in a
single guest's host vars to opt in; the host selects
`qwen38-139k-8-fp8-mtp` as its eight-slot main profile. The role downloads the
pinned Qwen3.5-9B Q4_K_M artifact through its existing cache-sync task, then
starts `llm-gpu-serving-small-c4-64k.service` on the next port after
`service_ports.llm_fast_api`. It probes `/v1/models` on both services before
the router renders the GPU deployment at order 1; the CPU small-pool member
remains order 3.

The main vLLM profile uses 75% of the 96 GiB device, leaving 24 GiB outside its
allocation for the small engine. Its 142,336-token maximum and eight slots
consume 1,138,688 KV tokens, below the estimated 1.165M-token pool after
reducing utilization from 85%; the profile also carries a 12 GiB vLLM runtime
reserve. This estimate is a configuration screen, not measured live acceptance.

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
