# llm_model_trial

Serves one run-scoped model candidate on the GPU host, restores the baseline serving unit afterwards,
and records the trial facts. The default flow serves a native vLLM or llama.cpp unit on the baseline guest.

## Container mode

Set `llm_model_trial_container_image` to a digest-pinned OCI reference to serve the candidate from a
container image instead. Container mode is off when the image is empty, and then the default flow runs
unchanged.

| Variable | Default | Meaning |
| --- | --- | --- |
| `llm_model_trial_container_image` | `""` | `name@sha256:<64 hex>`, optional tag before the digest. Enables container mode. |
| `llm_model_trial_container_env` | `{}` | Environment for the container. Values must not look like secrets (checked with `llm_model_trial_redact_regex`). |
| `llm_model_trial_container_args` | `[]` | Extra `vllm serve` arguments, one list item per argument. |
| `llm_model_trial_target_group` | `llm_model_trial_inventory_group` | Inventory group of the guest that runs the candidate. |
| `llm_model_trial_window_minutes` | `60` | Safety-net window, 1 to 60 minutes. |
| `llm_model_trial_fallback_probe_url` | `""` | Base URL probed for HTTP 200 on `/v1/models` before the baseline stops. Empty skips the probe. |

Validation runs on the controller in `preflight.yml` through `llm_model_trial_validate_container`:

- The image must match `[a-z0-9./-]+(:[A-Za-z0-9._-]+)?@sha256:[a-f0-9]{64}`.
- Container environment and arguments are rejected when no image is set.
- A target group other than the baseline group requires the image and exactly one host that is not the baseline guest.

### Play order

1. `localhost`: preflight, then add the candidate guest to `llm_model_trial_candidate_group`.
2. `llm_gpu_group` (container mode only): fallback probe, take the host lock, arm the restore timer, stop the baseline.
3. `llm_model_trial_candidate_group`: serve the candidate container, hold it, then stop and unlink it.
   Failures are recorded as facts and do not stop the run.
4. `llm_gpu_group` (container mode only): restore and verify the baseline, release the lock, stop and remove the
   restore timer, then fail the run if the candidate failed.

The restore timer starts the baseline `window_minutes` after the baseline stops. It is the safety net if the
run stops before step 4.

### Unit

The candidate unit runs `docker run` with:

- `--gpus all` and `--network host`
- one read-only volume, the candidate model directory, mounted at `/model`
- `--entrypoint vllm`, then `serve /model` with the bind host, port, and served model name
- no privileged flag and no other host path

The image must provide `vllm` on `PATH`. The candidate guest needs Docker with GPU access and the model
cache path used by the default flow.

## Installation

Install the role dependencies once per worktree with `ansible-galaxy install -r requirements.yml --force`.
Container mode needs Docker, installed by `docker_engine` or equivalent, with the NVIDIA container runtime on
the candidate guest. The default flow needs only the engines that `llm_gpu_serving` already installs.

## Usage

Run the trial playbook with the candidate settings as extra variables. Container mode needs only these additions:

```bash
scripts/run-ansible.sh playbooks/llm-model-trial.yml -i inventory/hosts.yml \
  -e hf_repo=example/model \
  -e llm_model_trial_container_image=registry.example/vllm/vllm-openai@sha256:<64 hex digits> \
  -e llm_model_trial_target_group=<inventory group of the candidate guest>
```

Omit the image to keep the default single-host flow. Omit the target group to run the candidate on the baseline
guest. Use `--tags llm_model_trial` for a scoped run.
