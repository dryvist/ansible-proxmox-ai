# Hermes agent — operations runbook

The Hermes agent is an autonomous LLM operator that runs a fleet of scheduled
jobs (Splunk sweeps, a daily fabric-status digest, GitHub triage, a nightly
wiki job) and answers ad-hoc requests over Slack. It reaches its brain through
the same serving fabric documented in [DEPLOYMENT.md](DEPLOYMENT.md); the brain
model itself is an OpenBao runtime value re-pointed with no rebuild — see "Brain
runtime source (OpenBao)" in the [`hermes_agent` role
README](../roles/hermes_agent/README.md). This doc covers the agent itself — its
cron fleet, its memory, the credentials it needs, and how the serving path
self-heals.

Everything here is seeded declaratively by the `hermes_agent` role. Every cron
run is a **fresh, isolated agent session** — there is no in-process state
carried between runs, so anything a job needs to remember it must write to
memory (below).

## Cron fleet

All jobs are defined in `roles/hermes_agent/defaults/main.yml` and seeded at
converge time via `hermes cron create` in `tasks/main.yml`. Schedules are UTC
(`hermes_agent_timezone: UTC`). Splunk jobs are gated on the Splunk MCP URL
**and** Slack tokens being present; GitHub triage on the issues PAT **and**
Slack tokens — a job whose credentials are unseeded is simply not created
(the role runs inert, never errors).

| Job | Schedule (UTC) | Purpose | Delivery |
| --- | --- | --- | --- |
| `homelab-ai-fabric-status` | `0 9 * * *` (daily 09:00) | Summarize AI-fabric health — router/gateway/DNS, merge-ready PRs | Slack |
| `hermes-nightly-wiki` | `0 2 * * *` (daily 02:00) | Lint + health-check the llm-wiki | default |
| `splunk-triage` | `3,18,33,48 * * * *` (every 15 min) | Broad self-directed anomaly sweep; `[SILENT]` unless something is off | alert → operator DM |
| `splunk-security` | `9,39 * * * *` (every 30 min) | Security lens — firewall drops, auth failures, honeypot hits, unexpected IPs | alert → operator DM |
| `splunk-parsing` | `24 * * * *` (hourly) | Data-quality lens — timestamp/line-merge/sourcetype/parse anomalies | alert → operator DM |
| `splunk-deepdive` | `44 */6 * * *` (every 6h) | Quiet RAG research — characterize one index → wiki + memory baseline | local, no alert |
| `splunk-digest` | `50 * * * *` (hourly) | Splunk heartbeat digest to the home channel; **never** `[SILENT]` | Slack (home) |
| `github-triage` | `12 */2 * * *` (every 2h) | Read-only dryvist-org PR/issue triage; report only, never mutate | Slack (home) |

Drift recovery: when the brain model changes, only *drifted* seeded jobs are
removed and re-seeded (`tasks/main.yml`); the canonical set is
`hermes_agent_seeded_cron_names`.

## Memory

- **Provider:** Hindsight (knowledge-graph + multi-strategy retrieval) in
  `local_external` mode — the standalone HA Hindsight service (two stateless
  replicas behind the Traefik pool at `hindsight.<sub>`, state in the
  dedicated ai-VLAN Postgres cluster), running alongside the always-on
  built-in `MEMORY.md` / `USER.md`. Set in `defaults/main.yml`
  (`hermes_agent_memory_provider: hindsight`, `hermes_agent_memory_mode:
  local_external`, `hermes_agent_memory_api_url`). The plugin config is
  rendered to `$HERMES_HOME/hindsight/config.json` (`mode` + `api_url`).
  Rollback: set `hermes_agent_memory_mode: local_embedded` and converge — the
  embedded-daemon path (hindsight-all in the venv, extraction LLM at the
  router) is still fully wired.
- **Persistence:** memory now lives in the ai-VLAN Postgres cluster (backed
  up under the database DR standard). The rest of `HERMES_HOME`
  (`/var/lib/hermes/.hermes`) — skills, profiles, the Kanban DB, sessions,
  logs, `MEMORY.md`/`USER.md` — remains the guest's durable surface on its
  snapshotted, replicated ZFS dataset.
- **Mode matters:** Hindsight defaults to a *cloud* mode that needs an API
  key. With no key, `is_available()` returns false and every memory tool
  call warns "Memory is not available" — a repeated, useless status line.
  An explicit mode (`local_external` today) + the rendered
  `hindsight/config.json` is what makes memory actually work. Verify with a
  non-fatal `hermes memory status` probe (run in `verify.yml`).

> If you see a runtime loop of a repeated memory status line (e.g.
> `Opening memory…Opening memory…`), that is the **brain degenerating**, not a
> memory bug — see "Repetition guard" below. The literal string is an upstream
> runtime line, not something this role emits.

## Credentials

Hermes reads its app credentials from OpenBao `secret/ai/hermes`, plus the
shared Splunk MCP connection from `secret/ai/mcp/splunk`. Both paths merge into
`bao_local_llm_secrets` with an env fallback. By design every field
defaults to empty and an empty value **disables that capability** rather than
failing the converge — so an un-seeded field silently turns a platform off.
That is the deliberate contract; there is no converge-time assertion that a
field is present.

| Field | Enables | Notes |
| --- | --- | --- |
| `SPLUNK_MCP_URL` + `SPLUNK_MCP_TOKEN` | the entire `splunk-*` cron fleet | sourced from shared `secret/ai/mcp/splunk`; published by ansible-splunk |
| `GH_PAT_WRITE_PROJECT_ISSUES` | `github-triage` cron + github-issues skill | empty until the token is issued |
| `HERMES_GITHUB_APP_ID` / `_INSTALLATION_ID` / `_PRIVATE_KEY` | GitHub-App docs-contributor / nightly-wiki path | empty until the App is provisioned |
| `HERMES_API_SERVER_KEY` | inbound job-submission API (`POST /v1/runs`, cron CRUD) | seeded programmatically; empty disables the api_server platform |
| `WEBHOOK_SECRET` | inbound webhook receiver | generate-if-absent; empty disables webhooks |
| `CONTEXT7_API_KEY` | Context7 MCP (on-demand library docs) | bao-first, env fallback |
| `ZAMMAD_API_TOKEN` | Zammad ITSM client | same token the zammad role seeds; empty until Zammad is deployed |
| `CODEX_AUTH_JSON` | Codex CLI for the isolated codex-runner user | create-only on the guest; empty until an operator seeds it |
| `OPENROUTER_API_KEY` | — | seeded but **parked** — not consumed by any role yet |

To verify seeding, the sanctioned path is the `hermes_agent` converge itself:
`verify.yml` proves a live tool-call round-trip through the router and, when
`HERMES_API_SERVER_KEY` is present, that the job API answers `/health` 200 and
refuses a keyless `POST /v1/runs` with 401. A missing Splunk token shows up as
the `splunk-*` crons simply not being seeded.

## Serving self-heal (the zombie watchdog)

The Mac serving host runs llama-swap under a launchd agent whose `KeepAlive`
only restarts on process **exit**. llama-swap can panic (`sync: WaitGroup is
reused`) into a process that stays alive and holds the listen socket but
answers nothing — a zombie launchd never notices — so every request gets
connection-refused and litellm surfaces `MidStreamFallbackError` until a human
intervenes.

The serving layer (in the nix-ai MLX module) now ships a **liveness watchdog**:
a launchd agent probes the proxy's own `/v1/models` every 60s and, on two
consecutive failures, `launchctl kickstart`s the server agent. It gates
re-fires with a cooldown marker so a 20–60s model reload is not
restart-stormed. Health, not PID. This is the durable fix for the recurring
`MidStreamFallbackError` outage; a manual `launchctl kickstart` remains the
sanctioned break-fix if the watchdog is not yet deployed.

## Repetition guard

The default brain (the real model id in `ai_default_model`) has its own tuned
`llm_router_large_models` entry carrying `repetition_penalty: 1.05` in
`extra_body`; because the router serves that real id (no alias indirection),
requests hit the tuned entry rather than falling through to the un-tuned `*`
wildcard. If 1.05 proves insufficient the next levers are `temperature ~1.0` /
`presence_penalty 0.0` in the same `extra_body`. Incident history: Zammad
(AI/LLM Serving).

## Cron schedule — review (proposed, pending operator decision)

The current fleet is anomaly-first and heavy on the single-stream brain
(concurrency 1). Points worth an operator decision — **none applied here**:

- **`splunk-digest` hourly, never `[SILENT]`** is the noisiest, lowest-signal
  job — 24 heartbeat posts/day to the home channel. Consider every 4–6h, or
  folding the heartbeat into the daily `homelab-ai-fabric-status`, keeping the
  alert-on-anomaly jobs (`triage`/`security`/`parsing`) as the real signal.
- **`splunk-triage` every 15 min** (96 runs/day) is aggressive for a
  single-stream brain. If contention shows up, 20–30 min keeps anomaly latency
  reasonable while freeing brain time.
- **Stagger heavy jobs off the same minute.** `triage`, `security`, `parsing`
  and `digest` can co-fire near the top of the hour; spreading their minutes
  avoids two long agentic runs hitting the one resident brain at once.
- **Self-directed work.** `splunk-deepdive` (quiet RAG, no alert) is the model
  for "propose your own work"; a second reflective job that reviews recent
  memory baselines and proposes follow-ups would extend that with no alert
  noise.

Keep the anomaly/security sweeps — a constant Splunk review is the highest-value
loop. The lever is cadence and staggering, not removal.

---

[docs.jacobpevans.com](https://docs.jacobpevans.com)
