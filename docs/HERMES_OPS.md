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

### Agentic direct-deliver digest crons

A second, fully declarative set of digest jobs lives in
`hermes_agent_direct_cron_jobs` and is reconciled by
`tasks/reconcile_direct_cron.yml`. Each is *agentic* — the gateway runs the
prompt and delivers the model's final response straight to Slack — with prompt
bodies pulled from the pinned `ai-llm-prompts` catalog (`prompt_file`). Their
Slack channel is never hardcoded: it comes from `HERMES_SLACK_DIGEST_CHANNEL`,
and the whole reconcile loop is skipped (with a loud warning) when that is unset.

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
- **Shared across profiles, by design.** Every named profile gets the same
  `hindsight/config.json` (mode + `api_url`), and neither sets `bank_id` nor
  `bank_id_template` — verified against the pinned hermes-agent's
  `plugins/memory/hindsight/__init__.py`: with `bank_id_template` unset,
  `_resolve_bank_id_template` always falls back to the static `bank_id`
  (default `"hermes"`), which every profile therefore shares. Moving a
  recurring card's `assignee` does **not** reset its memory continuity, and
  `daily-summary` (default) can still recall a moved job's findings. Memory
  is explicitly **not** part of the profile isolation boundary — do not rely
  on it to separate what one profile "knows" from another.

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

## Operating profiles

See `hermes_agent_profiles` in `defaults/main.yml` and "Operating profiles" in
the [role README](../roles/hermes_agent/README.md) for the concept, the
decision rule, and the current `splunk-admin` / `homelab-admin` profiles.
This section is the manual verification runbook.

### Profile smoke test (run once, after adding or changing a profile)

Not part of the converge — it burns a real LLM run, so it stays manual.
Verifies the three things the profiles design assumed and did not have a
live check for before this: the dispatcher actually spawns into the named
profile, a named-profile worker can post to Slack using the shared bot token
from **its own** `.env`, and its scoped MCP server(s) resolve.

```bash
sops exec-env secrets.enc.yaml 'doppler run -- \
  ansible-playbook -i inventory/hosts.yml playbooks/site.yml \
  --tags hermes_agent'

# On the guest, as the hermes user:
hermes profile list   # both profiles present
hermes kanban create 'profile smoke' --assignee splunk-admin \
  --body 'Post one line "splunk-admin profile smoke OK" to #hermes-all, then kanban_complete.' \
  --idempotency-key "profile-smoke-$(date -u +%Y-%m-%d)"
```

Confirm in order: (1) `kanban runs` shows the card dispatched with
`assignee=splunk-admin`, not `skipped_nonspawnable`; (2) the Slack post
arrives in #hermes-all; (3) the run's log shows the Splunk MCP resolving
(no "MCP server unavailable" for `splunk`). Repeat with `--assignee
homelab-admin` and a Zammad-shaped ask to cover the second profile.

### Memory-scope check (optional, only if the pinned hermes-agent version changes)

The "shared across profiles, by design" claim above was verified by reading
the pinned hermes-agent's hindsight plugin source, not by a live probe. If
`hermes_agent_version` ever moves, re-verify with:

```bash
# As the hermes user, in each profile:
hermes -p splunk-admin memory add "canary fact: splunk-admin wrote this"
hermes -p default memory recall "canary fact"   # should find it if global
```

If it does NOT find it, the plugin's default scoping changed upstream —
update the "shared across profiles" claim above and reconsider whether
`daily-summary` still needs to stay on the default profile.

### Add / remove a profile

Add: append an entry to `hermes_agent_profiles` (`mcp`, `env`, `skills`,
`soul_addendum_file`), add the matching `templates/soul-<name>.md.j2`
addendum, converge, then run the smoke test above before routing any real
card to it.

Remove: `hermes profile delete <name> -y` on the guest, then remove the
entry from `hermes_agent_profiles` and re-point every card that named it
back to `assignee: ""` (or another profile) in the same change — `assert.yml`
will fail the converge if a card still names a profile that no longer
exists. Only that profile's accumulated state (memories, wiki notes, session
history) is lost; the shared board, the default profile, and every other
profile are untouched.

### Concurrency: raising `max_in_progress`

`hermes_agent_kanban_max_in_progress` is pinned to `1` — the SUM cap across
every profile combined, not per profile (`max_in_progress_per_profile`
handles that split). This is today's measured-safe ceiling with a single
serving stream; naming more profiles does not raise it by itself. Raising it
back to 2+ (permitting real cross-profile overlap, at the measured ~0.71x
aggregate throughput) is a deliberate operator decision to make ONLY after
the serving tier is proven to have that capacity — never a side effect of
adding a profile.

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
