# vllm

vLLM OpenAI-compatible inference server on an NVIDIA GPU, in a privileged LXC.

Serves one model on the GPU guest and registers with the LiteLLM router as an
upstream, so the model appears in the chat UI that already exists.

## Status: converged but stopped

The NVIDIA guest sets `vllm_service_enabled: false` and serves through
`llama_cpp` on the Vulkan backend instead. This role stays converged as the
rollback path — the venv and weights remain on the guest, so reverting is a
variable flip, not a re-download.

## Why this role is not the one serving

**Corrected.** This section previously claimed llama.cpp publishes "no Linux
CUDA and no Linux ROCm release binary" and concluded llama.cpp could not use an
NVIDIA card. The ROCm half is **false** — upstream ships
`llama-*-bin-ubuntu-rocm-*-x64.tar.gz`, which the `llama_cpp` role already
selects for the AMD guest. The CUDA half is true and irrelevant: upstream also
ships a prebuilt Linux **Vulkan** binary that runs on NVIDIA. Do not re-derive
the old conclusion from the one true premise.

vLLM remains a good fit for weights it can read: official manylinux wheels with
CUDA bundled (`cp38-abi3`), so `pip install` is the whole install — no compiler,
no Docker, no NVIDIA Container Toolkit. It cannot read Qwen3.8-27B, whose
sub-4-bit weights exist only as GGUF and whose `qwen3_5` hybrid architecture has
no merged adapter in the out-of-tree GGUF plugin.

## Requirements

- The host runs the `nvidia_driver` role (`ansible-proxmox`), so the kernel
  module and `/dev/nvidia*` nodes exist and survive a reboot.
- The container has those device nodes bound in by `lxc_gpu_features`
  (`ansible-proxmox`), with majors resolved at run time.
- `tofu_data.constants.service_ports.llm_fast_api` resolves — the listen port
  comes from the tofu constants registry and is hard-required.

## Installation

Applied by `playbooks/site.yml` to hosts in `llm_vllm_group`, which
`inventory/load_tofu.yml` builds from the tofu `llm-vllm` tag.

```bash
nix develop --command doppler run -- \
  ./scripts/run-ansible.sh playbooks/site.yml --tags vllm --limit <gpu-guest>
```

## Usage

The service speaks the OpenAI API on `llm_fast_api`:

```bash
curl -s http://<gpu-guest>:<llm_fast_api>/v1/models
curl -s http://<gpu-guest>:<llm_fast_api>/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<vllm_served_model_name>","messages":[{"role":"user","content":"hello"}]}'
```

## The guest never installs a kernel module

An LXC shares the host kernel. This role installs NVIDIA **userspace** packages
only, from the same NVIDIA apt repository the host driver role uses, so the
guest's `libcuda.so` and the host's kernel module resolve from one source and
cannot drift apart. A version skew there presents as a CUDA init failure inside
the container while the host looks perfectly healthy.

Never add `cuda-drivers` or `nvidia-open` to `vllm_nvidia_userspace_packages` —
both pull DKMS and a kernel module.

## Model sizing

`vllm_max_param_billions` is a **per-guest** ceiling, asserted at converge.

The `llama_cpp` role carries an absolute ≥14B rule, but that rule is scoped to
the AMD serving guest after four host hard-locks on that node. It is neither
relaxed nor inherited here: this is different silicon on a different host, so
this guest states its own limit rather than silently escaping the other one.

The default model is AWQ 4-bit so the weights leave real KV-cache headroom on a
16 GB card. `vllm_max_model_len` is deliberately far below the model's native
context: KV cache grows linearly with context length and will evict the weights
that make the card fast.

## What a molecule run can and cannot cover

There is no GPU in a container, so the scenario covers the parameter-ceiling
assert, the rendered systemd unit, and the guards that skip every install task
under Docker. It does **not** exercise CUDA, the wheel install, or serving.
Those are verified on the real guest.
