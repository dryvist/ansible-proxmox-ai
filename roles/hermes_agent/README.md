# hermes_agent

Deploys the **[NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent)**
— the self-improving **autonomous agent** — headless in a dedicated LXC on the AI
VLAN.

> This is **not** the `ollama` / `open_webui` (`hermes-infer` / `hermes-chat`) LLM
> *serving* stack. Those serve the Hermes-4 *model*; this role runs the *agent*,
> which uses that model (or any OpenAI-compatible endpoint) as its brain.

## Installation

This role ships as part of this repository (`ansible-proxmox-ai`) — no
separate installation. The role itself fetches and sha256-verifies the pinned
Hermes installer on the target, so the LXC only needs base connectivity and
apt.

## Usage

Run the role against its inventory group:

```bash
doppler run -- uv run ansible-playbook \
  -i inventory/hosts.yml playbooks/site.yml \
  --tags hermes_agent
```

Split into focused pages, each covering one concern — start with
[Overview](docs/overview.md) for what it does, key variables, and what is
not yet live-validated:

- [Overview](docs/overview.md) — what it does, installation, usage, key
  variables, group/invocation, not-yet-live-validated.
- [Operating profiles](docs/operating-profiles.md) — named agent identities,
  the decision rule for when to add one, and how they isolate credentials
  and workload.
- [Knowledge base and GitHub](docs/knowledge-and-github.md) — the llm-wiki
  RAG index, the autonomous GitHub docs-contributor, the nix-hermes content
  bundle, and GitHub issues/projects access.
- [Splunk](docs/splunk.md) — operational log shipping, search access, and
  the self-directed 24/7 SIEM monitor.
- [Recurring work](docs/recurring-work.md) — the plain-cron reframe (Kanban
  is ad-hoc/follow-up only now) and the script-fed Splunk triage digests.
- [Vikunja bridge and API](docs/vikunja-and-api.md) — the operator-board-drives-Hermes
  bridge and the inbound job-submission API.
- [Curriculum and watchdog](docs/curriculum-and-watchdog.md) — the graded
  five-job eval, the per-platform runner-enforced tool policy, and the
  brain-health watchdog.
- [Brain and MCP](docs/brain-and-mcp.md) — brain model selection, Context7
  live docs, docs RAG search, and Codex escalation via MCP.

See also [docs/HERMES_OPS.md](../../docs/HERMES_OPS.md) for the operations
runbook (cron fleet, memory, credentials, serving self-heal) — this README
covers the role's configuration surface; that doc covers running it.
