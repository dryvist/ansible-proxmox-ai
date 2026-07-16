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

# Install required Ansible Galaxy collections
ansible-galaxy collection install -r requirements.yml
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

## Roles

### LLM serving

- `ollama` — Ollama model server
- `llama_cpp` — llama.cpp + llama-swap (GPU-tier serving)
- `llm_router` — LiteLLM proxy, the single OpenAI-compatible front door for
  the large/light serving tiers
- `open_webui` — Open WebUI chat frontend

### RAG (retrieval-augmented generation)

- `llamaindex` — Python + Ollama CPU-only embeddings pipeline
- `qdrant_docker` — Qdrant vector database (Docker in LXC)

### Agents

- `hermes_agent` — the autonomous NousResearch agent gateway
- `agent_exec` — sandboxed agent execution
- `agentgateway_docker` — agent gateway (Docker)
- `codex_runner` — isolated Codex CLI execution user

### LLM app platforms

- `dify_docker` — Dify LLM app platform
- `langflow_docker` — LangFlow visual LLM workflow builder
- `langgraph_docker` — LangGraph agent orchestration runtime
- `langfuse_docker` — Langfuse LLM observability/tracing

## Deploy orchestration (follow-up)

This repository currently ships **roles only** — there is no `site.yml` and no
inventory-loading playbook yet. Deploy orchestration (the site playbook, the
dynamic tofu-inventory loader, and the shared `docker_engine` / base-setup
role dependency the Docker-based roles here assume is already applied to the
host) is a tracked follow-up.

## Contributing

Commits follow [Conventional Commits](https://www.conventionalcommits.org/).
This repo uses the git-flow branching model: open pull requests against
`develop`, not `main`.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

---

<https://docs.jacobpevans.com>

[ci-badge]: https://github.com/dryvist/ansible-proxmox-ai/actions/workflows/ci-gate.yml/badge.svg
[ci-workflow]: https://github.com/dryvist/ansible-proxmox-ai/actions/workflows/ci-gate.yml
