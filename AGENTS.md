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
  **Registry rule (hard): every model name, alias, tier and enabled/servable
  state is written ONCE, in the repo-root `llm-models.yml` registry. The role's
  defaults and templates are projections of it — never add a model id, alias or
  OpenBao key field to `roles/llm_router/` (or anywhere else); add or edit a
  registry entry. A test fails the build when a registry value is re-typed in
  the role's defaults.**
  **Alias rule (hard): consumer-facing model aliases (`ai-default`,
  `ai-deep-analysis`, `claude-*`, any future tier name) live ONLY in
  `llm_router_model_group_aliases` (rendered as LiteLLM
  `router_settings.model_group_alias`). Registering an alias as its own
  `model_list` deployment entry — duplicating a physical entry's
  context_window/extra_body/api_base under a second name — is BANNED: the
  duplicate config silently drifts from the real backend every time the
  model changes (root cause of the #1004 diagnosis cost). One physical
  entry per backend; every other name is a literal alias with zero config.
  An alias is declared in `stable_aliases` on the registry entry it points
  at, which is what makes it structurally incapable of naming a model that
  is not registered.**
- `open_webui` — Open WebUI chat frontend

### RAG (retrieval-augmented generation)

- `llamaindex` — Python + Ollama CPU-only embeddings pipeline
- `qdrant_docker` — Qdrant vector database (Docker in LXC)

### Agents

- `herdr_server` / `herdr_remote` — herdr, the agent multiplexer the coding
  CLIs run inside: the runtime and its web/phone dashboard
  (`herdr-remote`), one guest each. The Slack bridge (`herdr-hail`) gets no
  guest of its own: it is a herdr PLUGIN that reads the runtime's control
  socket, so it runs as a companion unit beside it and `herdr_server` supplies
  its tokens.
  **Both guests are NixOS**, unlike everything else this repo
  converges. The rule that nix runs on the CONTROLLER and never on the guest
  still holds, and is what makes that workable: the roles install nothing.
  They call the shared `nixos_deploy` role, which runs
  `nixos-rebuild --target-host` on the controller and copies the closure over
  SSH, reusing the certificate `scripts/run-ansible.sh` already mints. Ansible
  stays the orchestrator; the config lives in
  [`nix-ai`](https://github.com/dryvist/nix-ai)'s `nixosModules.herdr`. Each
  role's own job is the handful of values NixOS cannot know — Slack tokens,
  agent credentials, the router endpoint — delivered as an `EnvironmentFile`
  at 0600, never the world-readable Nix store.
- `nixos_deploy` — generic controller-side NixOS converge (flake ref + host
  attribute). Reusable; nothing herdr-specific in it.
- `hermes_agent` — the autonomous NousResearch agent gateway
- `agent_exec` — sandboxed agent execution
- `agentgateway_docker` — agent gateway (Docker)
- `codex_runner` — isolated Codex CLI execution user

### LLM app platforms

- `dify_docker` — Dify LLM app platform
- `langflow_docker` — LangFlow visual LLM workflow builder
- `langgraph_docker` — LangGraph agent orchestration runtime
- `langfuse_docker` — Langfuse LLM observability/tracing

### Ops

- `fabric_watchdog` — 2-minute systemd timer on the Hermes guest probing the
  MCP fabric + LLM front door from Hermes's own network path; alerts once per
  up/down transition over Slack. Deliberately Slack-only, not ntfy/Prometheus:
  those run on the observability node, so they can't report that node's own loss.

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
`ansible-proxmox-apps`. The shared `inventory_resolve` role ships in the
`dryvist.homelab` collection — install dependencies once per fresh worktree:

```bash
ansible-galaxy install -r requirements.yml
```

Use `install`, not `collection install` or `role install`: each of those
silently ignores the other section of `requirements.yml` and still exits zero.

### Commands

`scripts/run-ansible.sh` mints a short-lived SSH certificate from the OpenBao
CA (`ssh-certificate-authority` ADR) when `BAO_ADDR` +
`OPENBAO_APPROLE_ANSIBLE_ROLE_ID`/`_SECRET_ID` are ambient, then runs the
playbook — the same signing token also satisfies `inventory_resolve`'s
`BAO_TOKEN` requirement, so no separate token is needed. Falls back verbatim
to the static `PROXMOX_SSH_KEY_PATH` flow when that env is absent. See
[SSH certificate access](https://docs.jacobpevans.com/d/runbooks/ssh-certificate-access).

```bash
# Converge everything (Doppler injects BAO_ADDR + the ansible-converge and
# local-llm AppRole creds, PROXMOX_SUBDOMAIN, PROXMOX_SSH_KEY_PATH, ...)
doppler run -- scripts/run-ansible.sh playbooks/site.yml -i inventory/hosts.yml --forks 25

# Scoped converge — --limit MUST include localhost (the inventory loader runs
# on localhost via add_host; without it no hosts are added and every play
# reports "no hosts matched")
doppler run -- scripts/run-ansible.sh playbooks/site.yml -i inventory/hosts.yml \
  --tags llm_router --limit llm_router_group,localhost --forks 25

# Lint
ansible-lint
```

The OpenBao secrets pre-fetch play is tagged `always`, so scoped `--tags`
runs get their secrets automatically — no `--tags openbao_secrets,<role>`
pairing is needed (unlike ansible-proxmox-apps).

`scripts/run-ansible.sh` also refuses to converge from a checkout that is
behind its tracked branch — a stale checkout deploys old content and still
exits 0 with a green play recap, with nothing in the output to tell the
difference. `ALLOW_STALE_CHECKOUT=1` is the deliberate escape hatch for a
pinned replay. **The guard covers a stale branch checkout, not every way a
checkout can be wrong.** On a detached HEAD (how GitHub Actions checks out a
PR, or a manual `git checkout <sha>`) there is no tracked branch to compare
against, so the guard skips the staleness comparison entirely rather than
erroring — before that exemption was added (#503), a detached checkout hit
an unrelated git error (`origin/HEAD` failing to resolve) and aborted the
whole converge, misread as a guard failure. Detached-HEAD converges are
intentionally ungated by this check; verify a pinned-commit run some other
way if staleness matters there.

### Two execution paths

1. **Provisioning-driven (tofu-proxmox first).** Guest shells, DNS, and the
   published inventory come from the `tofu-proxmox` Terrakube workspace: an
   apply publishes `ansible_inventory` to the RustFS S3 store. After any infra
   change, run that workspace first, then converge from here — `load_tofu.yml`
   resolves the fresh artifact automatically.
2. **Direct local converge (day-to-day app changes).** No tofu run needed
   when guests haven't changed: converge directly with the commands above.
   Inventory resolution is owned by the shared `inventory_resolve` role; its
   [README](https://github.com/dryvist/homelab-contracts/tree/main/ansible/roles/inventory_resolve)
   is the canonical description.

### Shared-role duplication (deliberate)

- `docker_engine`: local copy; apps keeps its own for its remaining Docker
  roles.
- `openbao_secrets`: local copy trimmed to the `local-llm` domain; apps keeps
  the full multi-domain copy. Promoting both into `homelab-contracts` is a
  tracked follow-up.

### Rolling converge for pooled services

Any play whose group is a multi-node, health-checked pool (`llm_router_group`,
`hindsight_group`, and `agentgateway_group` once it grows past one node) must
converge one member at a time. Restarting every member in the same play takes
the service down even though the pool exists to prevent exactly that.

The pattern is four lines plus the shared gate — copy it, do not re-invent it:

```yaml
  serial: 1
  max_fail_percentage: 0
  pre_tasks:
    - name: Gate on pool-member reachability
      ansible.builtin.import_tasks: tasks/pool_member_gate.yml
```

The role supplies the health gate itself: end the role with a local liveness
check after `meta: flush_handlers`, so the member is verified serving inside
its own rolling window before the next one is touched. Both current pooled
roles already do this (`llm_router` liveness, `hindsight_docker` /health).

Outcomes, by design:

| Situation | Result |
| --- | --- |
| Member never answered (node down, guest stopped) | Skipped by the gate, converge rolls on |
| Member restarted, then unhealthy or unreachable | Play stops before the next member is touched |

Do not add these keywords to a single-node play — the gate would turn a down
host into a silent skip. Adopt them in the same change that adds the 2nd node.

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
ansible-galaxy install -r requirements.yml

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
