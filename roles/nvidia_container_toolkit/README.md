# nvidia_container_toolkit

Gives Docker on an NVIDIA guest GPU access. Applied only through
`playbooks/llm-gpu-container-trial.yml`, to guests in
`llm_gpu_container_trial_group` (the `llm-container-trial` guest tag).

## Installation

No separate install step. The playbook applies `docker_engine` and then this role
to each opted-in guest. The NVIDIA userspace comes from `nvidia_gpu_guest`, which
must already be converged on the guest.

Prerequisites on the guest:

- The GPU device nodes are passed into the LXC.
- Docker can pull the sample image. The verification pull runs in the Docker
  daemon, which the role does not proxy.

## Usage

Converge only the opt-in group:

```bash
scripts/run-ansible.sh playbooks/llm-gpu-container-trial.yml -i inventory/hosts.yml \
  --limit llm_gpu_container_trial_group,localhost
```

The role does this, in order:

1. Adds NVIDIA's container toolkit apt repository (`nvidia_container_toolkit_repo_uri`)
   and installs `nvidia-container-toolkit` at the pinned
   `nvidia_container_toolkit_package_version`.
2. Registers the `nvidia` runtime in `/etc/docker/daemon.json` with
   `nvidia-ctk runtime configure`, only when it is missing. A change restarts Docker.
3. Sets `no-cgroups = true` in the runtime config (`nvidia_container_toolkit_no_cgroups`),
   because the guest is an LXC container.
4. Generates the CDI specification into `nvidia_container_toolkit_cdi_spec_path`.
   The spec is regenerated on every converge and replaced only when it changed.
5. Runs `nvidia-smi -L` in a throwaway `--rm` container from
   `nvidia_container_toolkit_sample_image` (NVIDIA's documented sample image,
   pinned by digest) and asserts that it lists `GPU 0:`. Skipped under `--check`.

The package pin is changed by hand. Check it against NVIDIA's apt index before
bumping, and keep the pin at least a week after its release.
