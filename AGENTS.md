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
`ansible-proxmox-apps` consumes. The schema and the upstream desired-state
contract are documented once, upstream — this repo does not duplicate them.
`inventory/load_tofu.yml` here maps the containers section of that contract
onto this repo's AI groups (tag -> group); `inventory/group_vars/*.yml` only
carries this repo's AI-role group defaults (restart policies, subdomain/
API-key lookups). Neither defines or owns the inventory schema itself.

## Secrets Management

**Runtime injection**: Doppler (`doppler run --`)
**At-rest encryption**: SOPS + age

**Roles are injection-agnostic.** Every role reads a secret as plain
`lookup('env', 'KEY')` (with an OpenBao-first, env-fallback pattern in
group_vars where applicable) and doesn't know or care where the value came
from — never bake a specific backend (OpenBao, Doppler, SOPS) into a role
default. The secrets architecture itself (which store holds what, per-domain
RBAC) is documented on the docs site, not here.

## Deploy orchestration

This repo is fully self-sufficient: `playbooks/site.yml` +
`inventory/load_tofu.yml` converge the AI fleet with no dependency on
`ansible-proxmox-apps`. The shared `inventory_resolve` galaxy role is
gitignored — install it once per fresh worktree:

```bash
ansible-galaxy collection install -r requirements.yml
ansible-galaxy role install -r requirements.yml   # -> roles/inventory_resolve
```

### Commands

```bash
# Converge everything (Doppler injects BAO_ADDR + the local-llm AppRole creds,
# PROXMOX_SUBDOMAIN, PROXMOX_SSH_KEY_PATH, ...)
doppler run -- ansible-playbook -i inventory/hosts.yml playbooks/site.yml --forks 25

# Scoped converge — --limit MUST include localhost (the inventory loader runs
# on localhost via add_host; without it no hosts are added and every play
# reports "no hosts matched")
doppler run -- ansible-playbook -i inventory/hosts.yml playbooks/site.yml \
  --tags llm_router --limit llm_router_group,localhost --forks 25

# Lint
ansible-lint
```

The OpenBao secrets pre-fetch play is tagged `always`, so scoped `--tags`
runs get their secrets automatically — no `--tags openbao_secrets,<role>`
pairing is needed (unlike ansible-proxmox-apps).

### Two execution paths

1. **Provisioning-driven (tofu-proxmox first).** Guest shells, DNS, and the
   published inventory come from the `tofu-proxmox` Terrakube workspace: an
   apply publishes `ansible_inventory` to the RustFS S3 store and its
   after-hook refreshes the local gitignored cache. After any infra change,
   run that workspace first, then converge from here — `load_tofu.yml`
   resolves the fresh artifact automatically.
2. **Direct local converge (day-to-day app changes).** No tofu run needed
   when guests haven't changed: converge directly with the commands above.
   Inventory resolution order is `TOFU_INVENTORY_PATH` (explicit pin) →
   RustFS published artifact → local gitignored cache (only with
   `TOFU_INVENTORY_ALLOW_STALE=1`). While the published artifact is missing
   (apps#975), pin explicitly:
   `TOFU_INVENTORY_PATH=$HOME/git/public/homelab/ansible-proxmox-apps/main/inventory/tofu_inventory.json`.

### Shared-role duplication (deliberate)

- `docker_engine`: local copy; apps keeps its own for its remaining Docker
  roles.
- `openbao_secrets`: local copy trimmed to the `local-llm` domain; apps keeps
  the full multi-domain copy. Promoting both into `homelab-contracts` is a
  tracked follow-up.

## Testing

| Check | Command | When |
| --- | --- | --- |
| Ansible lint | `ansible-lint` | pre-commit, every PR |
| Playbook syntax | `ansible-playbook playbooks/site.yml --syntax-check` | every PR (CI) |
| Inventory load | see below | every PR (CI) |
| Molecule (per scenario) | `molecule test -s llamaindex` / `-s qdrant` | every PR (CI); locally before merging role changes (needs Docker) |

**Inventory-load validation locally:**

```bash
TOFU_INVENTORY_PATH=$PWD/tests/inventory_load/tofu_inventory.json \
  ansible-playbook tests/inventory_load/verify_inventory.yml \
  -i inventory/hosts.yml -c local
```

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
