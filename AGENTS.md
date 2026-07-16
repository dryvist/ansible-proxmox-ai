# Ansible Proxmox AI — AI Agent Documentation

Configure the homelab's AI/LLM applications on Proxmox VMs and LXC containers.
VMs/containers are provisioned by `tofu-proxmox`; this repo handles app
config only. It was extracted from `ansible-proxmox-apps` to give the AI/LLM
stack its own lint/CI/release lifecycle, independent of the media/network/
observability roles that stay in `ansible-proxmox-apps`. This repo starts from
a single squashed genesis commit; granular pre-split history for every role
remains in `ansible-proxmox-apps`' git log (`git log --follow <path>`).

## This Repo Owns

### LLM serving

- `ollama` — Ollama model server
- `llama_cpp` — llama.cpp + llama-swap (GPU-tier serving)
- `llm_router` — LiteLLM proxy, the single OpenAI-compatible front door for
  the large/light serving tiers.
  **Alias rule (hard): consumer-facing model aliases (`ai-default`,
  `ai-deep-analysis`, `claude-*`, any future tier name) live ONLY in
  `llm_router_model_group_aliases` (rendered as LiteLLM
  `router_settings.model_group_alias`). Registering an alias as its own
  `model_list` deployment entry — duplicating a physical entry's
  context_window/extra_body/api_base under a second name — is BANNED: the
  duplicate config silently drifts from the real backend every time the
  model changes (root cause of the #1004 diagnosis cost). One physical
  entry per backend; every other name is a literal alias with zero config.**
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

**This repo does NOT own** Splunk (`ansible-splunk`), Cribl/media/network/DNS
roles, or non-AI observability (`ansible-proxmox-apps`), or Proxmox host
config (`ansible-proxmox`).

## Inventory

This repo is a **read-only consumer** of the shared published tofu inventory
contract — the same `ansible-inventory-v1` schema (RustFS-published artifact,
`tofu_data.constants`, `TOFU_INVENTORY_PATH` override) that
`ansible-proxmox-apps` consumes. The schema, its resolution priority, and the
upstream desired-state contract are documented once, upstream — this repo
does not duplicate them. `inventory/group_vars/*.yml` here only carries this
repo's AI-role group defaults (restart policies, subdomain/API-key lookups);
it does not define or own the inventory schema itself.

## Secrets Management

**Runtime injection**: Doppler (`doppler run --`)
**At-rest encryption**: SOPS + age

**Roles are injection-agnostic.** Every role reads a secret as plain
`lookup('env', 'KEY')` (with an OpenBao-first, env-fallback pattern in
group_vars where applicable) and doesn't know or care where the value came
from — never bake a specific backend (OpenBao, Doppler, SOPS) into a role
default. The secrets architecture itself (which store holds what, per-domain
RBAC) is documented on the docs site, not here.

## Deploy orchestration (follow-up)

This repo currently ships **roles only** — there is no `site.yml`, no
`load_tofu.yml`, and no playbook wiring yet. Deploy orchestration (the
site playbook, the dynamic inventory loader, and the shared
`docker_engine` / base-setup role dependency every Docker-based role here
assumes is already applied to the host) is a tracked follow-up, not part of
this repo yet. Until then, these roles are consumed by `ansible-proxmox-apps`
or invoked directly against a prepared host.

## Testing

| Check | Command | When |
| --- | --- | --- |
| Ansible lint | `ansible-lint roles/` | pre-commit, every PR |
| Molecule (per scenario) | `molecule test -s llamaindex` / `-s qdrant` | every PR (CI); locally before merging role changes (needs Docker) |

```bash
# Install Ansible Galaxy dependencies (once)
ansible-galaxy collection install -r requirements.yml

# Run one scenario's full test cycle (create -> converge -> idempotence -> verify -> destroy)
molecule test -s llamaindex
molecule test -s qdrant

# Or step through individually for debugging
molecule converge -s qdrant
molecule verify -s qdrant
molecule destroy -s qdrant
```

## Dev Environment

This repo uses [Nix flakes](https://wiki.nixos.org/wiki/Flakes) +
[direnv](https://direnv.net/) for a reproducible dev environment.

```sh
direnv allow    # one-time per worktree — auto-activates on cd
```

The shell is provided by the `ansible-apps` shell in
[nix-devenv](https://github.com/JacobPEvans/nix-devenv) via `.envrc`. There is
no local `flake.nix` — direnv fetches and caches the remote shell
automatically.

To activate manually without direnv:

```sh
nix develop "github:JacobPEvans/nix-devenv#ansible-apps"
```

### Tools provided

- ansible, ansible-lint, molecule — configuration management
- sops, age — secrets management
- python3 with paramiko, pyyaml, jinja2, jsondiff — Ansible dependencies
- jq, yq, pre-commit — utilities
