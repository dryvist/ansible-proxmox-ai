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

## A converge that looks wedged restarting the gateway

`Restart hermes-gateway` runs `hermes-cron-drain-restart`, which pauses every
cron store, waits for in-flight runs to finish, restarts, and lifts only the
pauses it took. A converge sitting on `waiting on N in-flight run(s)` is that
wait working — it is bounded by `hermes_agent_cron_drain_timeout_seconds` and
may legitimately run that long. Stop it for a *stall* (the same job names, the
count not dropping), never for a duration.

To get out early, in this order:

1. `SIGTERM` the wrapper, or Ctrl-C the play. Its own release path runs and
   lifts every pause it owns.
2. Only if the process is already gone, remove the sentinels by hand
   (`$HERMES_HOME/ESTOP`, and the same under each `profiles/*/`).
3. Do neither and the next converge clears them once they age past the drain
   bound. That is a backstop, not a plan.

**Do not remove a sentinel while the wrapper is still running.** It will not
crash — the release tolerates a file that is already gone — but it un-pauses
the fleet *before* the restart lands, which is the exact damage the drain
exists to prevent, and the wrapper will still report `released N pause(s)`
because that count is what it owned, not what it removed. By-hand clearing
leaves no trace in the wrapper's output.

A pause that outlives a converge is not fatal on its own: the sentinel carries
an owner marker and a timestamp, so the next converge clears its own orphan and
says how stale it was. Anything it cannot prove is its own — an operator's
pause, an empty file from `touch`, an unparseable one — is left alone and
logged. `hermes resume` still lifts any of them by hand.

## Not yet live-validated

Verify on the first converge: (a) `install.sh` runs clean non-interactively as root
on a minimal Debian LXC; (b) `hermes gateway run --replace` stays up headless with no
messaging platform; (c) the role installs its pinned Hindsight client in the Hermes
venv and completes a read-only recall against the rendered agent bank.
