# ansible-proxmox-ai

Ansible roles for the homelab's AI/LLM stack on Proxmox VMs and LXC containers.
Extracted from `ansible-proxmox-apps`; granular pre-split commit history for
each role lives in that repo's git log (`git log --follow <path>`).

[![CI][ci-badge]][ci-workflow]
![License](https://img.shields.io/badge/license-Apache%202.0-blue)

## Installation

This repository owns its toolchain via a Nix flake + direnv (`ansible`,
`ansible-lint`, `molecule`, `sops`, `age`, `python3` with paramiko/pyyaml/jinja2,
`jq`, `yq`, `pre-commit`). Run everything inside that dev shell.

```bash
git clone <repo-url> ansible-proxmox-ai
cd ansible-proxmox-ai
direnv allow    # one-time per worktree — auto-activates the dev shell on cd

# Install required Ansible Galaxy dependencies
ansible-galaxy install -r requirements.yml --force
```

To activate the dev shell manually without direnv:

```bash
nix develop "github:JacobPEvans/nix-devenv#ansible-apps"
```

## Usage

```bash
# Lint every role
ansible-lint roles/

# Test a single scenario end to end (needs Docker)
molecule test -s llamaindex
molecule test -s qdrant
```

Pull requests into `develop` run the scenarios matched by the shared
Ansible CI role map. Shared inputs or an unmapped change widen to the full
matrix. Pull requests into `main` run the full Molecule matrix. Pushes to
`main` or `develop` skip Molecule and the Data Contract job; `Merge Gate`
allows both to be skipped only on pushes and on pull requests into `develop`.

## Roles

### LLM serving

- `ollama` — Ollama model server
- `llama_cpp` — llama-server, direct (GPU-tier serving)
- `llm_gpu_serving` — profile-based vLLM and llama.cpp GPU serving on `llm_gpu_group`, including the pinned guest NVIDIA userspace
- `nvidia_container_toolkit` — opt-in Docker GPU access (NVIDIA container toolkit, pinned) on guests
  tagged `llm-container-trial`, converged only by `playbooks/llm-gpu-container-trial.yml`
- `llm_router` — LiteLLM proxy, the single OpenAI-compatible front door for
  the large/light serving tiers
- `open_webui` — Open WebUI chat frontend

### RAG (retrieval-augmented generation)

- `llamaindex` — Python + Ollama CPU-only embeddings pipeline
- [`qdrant_docker`](roles/qdrant_docker/README.md) — Qdrant vector database (Docker in LXC)

### Agents

- `hermes_agent` — the autonomous NousResearch agent gateway
- `agent_exec` — sandboxed agent execution
- `agentgateway_docker` — agent gateway (Docker)

### LLM app platforms

- `dify_docker` — Dify LLM app platform
- `langflow_docker` — LangFlow visual LLM workflow builder
- `langgraph_docker` — LangGraph agent orchestration runtime
- `langfuse_docker` — Langfuse LLM observability/tracing
- `phoenix_docker` — Arize Phoenix LLM tracing/evals/experiments. An OTLP
  sink fed by Cribl Stream, peer of Langfuse: producers never point at it
  directly (see `ai_orchestration_otel_endpoint` in `inventory/group_vars/all/`).
  Elastic License 2.0 — unlimited for internal use, no license key.

### Ops

- `fabric_watchdog` — 2-minute systemd timer on the Hermes guest probing the
  MCP fabric + LLM front door from Hermes's own network path; alerts once per
  up/down transition to the shared ntfy alert hub.

## Deploy orchestration

`playbooks/site.yml` converges the fleet, and `inventory/load_tofu.yml` loads
the dynamic tofu inventory. AGENTS.md (Deploy orchestration) has the commands.
The Docker-based roles still depend on the local `docker_engine` bootstrap copy
(imported from `ansible-proxmox-apps`).

## Contributing

### Model admission

Model trials and enabled router entries use `model-policy.json` from the
installed `dryvist.homelab` collection's `llm_model_catalog/files/` directory.
The policy records model IDs, upstream IDs and creation dates, dossiers,
an evaluation date, a maximum age of 365 days, and denied IDs.
The controller's current UTC date enforces model age.

The `llm_model_trial_policy` role input supplies the same schema when the
installed collection has no policy file. Its default is empty and admits no
models. Tests supply synthetic evidence through fixtures. An installed policy
takes precedence over the role input.

Trial preflight reads `cardData.base_model` recursively with the native Hub
CLI. Derived models inherit their oldest upstream creation date. Trial facts
record that date, upstream ID, and dossier. Router projections exclude old,
denied, undated, and unlisted models.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/).
This repo uses the git-flow branching model: open pull requests against
`develop`, not `main`.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

---

<https://docs.jacobpevans.com>

[ci-badge]: https://github.com/dryvist/ansible-proxmox-ai/actions/workflows/ci-gate.yml/badge.svg
[ci-workflow]: https://github.com/dryvist/ansible-proxmox-ai/actions/workflows/ci-gate.yml

## CI

Pull requests into `develop` use changed-role Molecule selection with lint,
syntax, and contract checks. Pull requests into `main` run the full matrix.
Pushes to `main` and `develop` skip Molecule and the Data Contract job.
CI-harness and GPU contract-fixture changes select the `llm_gpu_serving` smoke
scenario; other unclassified paths widen to the full matrix. The Data Contract
selector sends unmapped roles and unrecognized paths to the full contract
suite and fails on an unmapped test playbook; a removed test file selects its
owner's scope, if any. Runner jobs cap at ten minutes, or at sixty on pull
requests from `develop` into `main` in this repository; the Renovate checksum
fix job caps at five. Required validation is aggregated by `Merge Gate`.
Public pull-request CI stays on GitHub-hosted runners.

See the canonical policy in the `JacobPEvans/.github` README, “Ansible CI policy.”
