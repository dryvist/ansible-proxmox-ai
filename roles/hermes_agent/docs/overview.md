# Overview

## What it does

- Installs Hermes Agent system-wide via the official installer (bundles Python/uv +
  Node), once, behind a `creates` guard. The installer is fetched from the pinned
  release tag's raw URL and **sha256-verified before it runs** — never
  `curl <url> | bash` of a moving remote script — and `--branch <tag>` pins the
  app checkout to the same release. The Hermes daemon owns subsequent updates
  (`hermes update`) — Ansible owns only the platform, so converge stays idempotent.
- Runs the `hermes gateway` daemon under a dedicated non-root `hermes` user via
  systemd. The gateway drives the built-in **cron** scheduler and the **Kanban**
  dispatcher (autonomy) even with no messaging platform configured.
- Runs the separately authenticated `hermes dashboard` service on the published
  dashboard port. Traefik exposes it at the no-port `hermes` hostname; the
  existing `/webhooks/` route and `hermes-api` endpoint remain machine-only.
- `HERMES_HOME` (`/var/lib/hermes/.hermes`) lives on a dedicated ZFS data volume —
  memory, skills, profiles, the Kanban DB, sessions and logs — so it is snapshotted
  and replicated to the DR node (the agent's accumulated knowledge is irreplaceable).
- Points the model backend at the LiteLLM router (`Qwen3-Coder-30B-A3B` via
  `llm.<subdomain>/v1`, OpenAI-compatible, 262144 context); sets memory provider to **Hindsight** (best self-hostable
  June 2026) alongside the always-on `MEMORY.md`/`USER.md`; caps `agent.max_turns`
  so a runaway loop can't pin the GPU overnight.
- Wires the Slack gateway (Socket Mode) via five env vars in `.env`, read
  directly by Hermes' own Slack adapter — no `config.yaml` changes needed.
  All five default to empty, so the gateway simply runs Slack-free until they
  are set.
- Seeds a daily cron job that summarizes the homelab AI fabric status and posts
  it to the Slack home channel; activation happens on the next converge.

Installation and usage are covered in the [role README](../README.md); this
page continues with the variable surface.

## Key variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `hermes_agent_home` | `/var/lib/hermes` | data-volume mount = the user home |
| `hermes_agent_model_base_url` | `https://llm.{{ PROXMOX_SUBDOMAIN }}/v1` (router) | the brain endpoint |
| `hermes_agent_model` | `Qwen3-Coder-30B-A3B` | model id (LiteLLM router alias) |
| `hermes_agent_memory_provider` | `hindsight` | external memory provider |
| `hermes_agent_max_turns` | `90` | agentic-loop budget |
| `hermes_agent_slack_bot_token` | `""` | Slack bot OAuth token (`xoxb-…`) |
| `hermes_agent_slack_app_token` | `""` | Slack app-level token for Socket Mode (`xapp-…`) |
| `hermes_agent_slack_allowed_users` | `""` | comma-sep Slack member IDs allowed to DM the bot |
| `hermes_agent_slack_home_channel` | `""` | Slack channel ID for proactive posts |
| `hermes_agent_slack_home_channel_name` | `""` | Slack channel display name |

## Group / invocation

Targets `hermes_agent_group`, derived from the `hermes-agent` tag in `load_tofu.yml`.
Run via `site.yml` (`--tags hermes_agent`).

## Not yet live-validated

Verify on the first converge: (a) `install.sh` runs clean non-interactively as root
on a minimal Debian LXC; (b) `hermes gateway run --replace` stays up headless with no
messaging platform; (c) Hindsight initialises from `config.yaml` alone (it may need
its client package on first run — `memory status` check is non-fatal so it surfaces
without failing the converge).
