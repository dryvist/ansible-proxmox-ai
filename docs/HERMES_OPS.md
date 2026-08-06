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

Everything here is seeded declaratively by the `hermes_agent` role. Every run is
a **fresh, isolated session** — there is no in-process state carried between
runs, so anything a job needs to remember it must write to memory (below). That
property held under the Kanban-board design (#83) and holds equally under the
native-cron design below: neither carries state in-process.

## Cron fleet

Schedules are UTC (`hermes_agent_timezone: UTC`). Everything below is defined
in `roles/hermes_agent/defaults/main.yml` — this doc restates it and can drift,
so **the defaults file wins on any disagreement**.

### Recurring reports are plain cron jobs, not Kanban cards (native-cron reframe)

Issue #83 moved recurring work off long-lived agentic cron sessions (state
accumulated between runs; corrupted compression bookkeeping crash-looped the
`splunk-*` jobs, INC-17120) onto a two-layer Kanban design: a script-only
enqueuer cron created one card per slot, and the board engine dispatched each
in a fresh worker session. That fixed the state-leak, but the enqueuer itself
became a silent-failure hazard: the worker had to run `hermes kanban archive`
as its own last action to free its idempotency key for the next fire, and
skipping that step for any reason (model forgets, a runtime cap killing the
process, any flake) left the key claimed forever and the job silently dark —
`hermes kanban complete` transitions `running/ready -> done`, never
`archived`, and no atomic native alternative exists.

The reframe underneath that: a recurring **report** (digest, sweep, status
check) was never a discrete unit of work a human expects to see tracked to
completion on a board — it's a scheduled broadcast. So every recurring report
is now a plain `hermes cron create` job whose prompt is delivered straight to
Slack (`--deliver`), reconciled by the *existing* `tasks/reconcile_direct_cron.yml`
mechanism (`hermes_agent_direct_cron_jobs`) — no new machinery. Each prompt
keeps its retrieval-first shape verbatim: recall a named memory key at start,
act only on the delta, reply `[SILENT]` if nothing changed, save the updated
fingerprint back to the same key at the end — the gateway's native
`[SILENT]`/`NO_REPLY` marker suppresses delivery, so a quiet run posts nothing.

Only one workload stayed a genuine Kanban card: **docs-sync** (below), because
its idempotency problem has a deterministic answer that does not depend on the
model. Every other pre-reframe card is now a direct-cron job:

| Job | Schedule (UTC) | Deliver |
| --- | --- | --- |
| `homelab-ai-fabric-status` | `4 8-22 * * *` | `#hermes-issues` |
| `hermes-nightly-wiki` | `0 2 * * *` | `#hermes-all` |
| `daily-summary` | `0 12 * * *` | `#hermes-all` |
| `zammad-review` | `41 */2 * * *` | `#hermes-all` |
| `splunk-triage` | `7 * * * *` | `#hermes-all` |
| `splunk-security` | `22 */6 * * *` | `#hermes-all` |
| `splunk-parsing` | `37 2 * * *` | `#hermes-all` |
| `splunk-deepdive` | `11 3 * * *` | `#hermes-all` |
| `github-triage` | `26 */6 * * *` | `#hermes-all` |
| `bot-pr-triage` | `43 */6 * * *` | `#hermes-all` |
| `review` | `0 */8 * * *` | `#hermes-all` |
| `anomaly-hunt` | `13 */12 * * *` | `#hermes-all` |
| `docs-study` | `43 5 * * *` | `#hermes-all` |
| `ai-news` | `19 0,12,16,19 * * *` | `#hermes-noise` |
| `daily-innovation` | `47 6 * * *` | `#hermes-noise` |
| `app-seeding` | `53 7 * * *` | `#hermes-all` |
| `fleet-health` | `3 10 * * 1` (weekly) | `#hermes-all` |

Every job is additionally **capability-gated**: all require the Slack bot
token, app token and home channel; the `splunk-*` jobs also require
`hermes_agent_splunk_monitor_enabled` and the Splunk MCP URL. A job whose gate
is false is never created — the role runs inert, never errors.

`homelab-ai-fabric-status` previously split its report by outcome (all-clear to
the noise channel, a break to issues). A plain `--deliver` target cannot
conditionally choose a destination, so this card now always posts to
`#hermes-issues` — louder-by-default on a healthy run, a deliberate accepted
trade-off rather than a silent regression.

`tasks/main.yml` actively *removes* the superseded per-card enqueuer crons
(`hermes_agent_superseded_kanban_enqueuer_cron_names`, the old `<job>-enqueue`
names plus `kanban-enqueue-safety-net`) and *pauses* (not deletes) the three
`-v2` direct-cron jobs the reframe replaces 1-for-1
(`zammad-incident-review-v2`, `github-org-triage-v2`, `daily-operator-summary-v2`),
so a guest converged mid-migration does not double-fire.

### The one remaining Kanban card (`hermes_agent_kanban_cards`)

**`docs-sync`** stays a genuine Kanban card, weekly (`13 8 * * 1`), because
docs-sync is a discrete unit of work — open a PR, wait for review — that the
operator expects tracked to completion on the board, not just broadcast. Its
crontab entry (`tasks/reconcile_kanban_card_cron.yml`) runs `hermes kanban
create` directly with a **PER-RUN** idempotency key (`docs-sync-<UTC-date>`),
not a stable one, and nothing in the run depends on the model archiving
anything. Two things ruled a stable key out: (1) it requires the model to run
`hermes kanban archive` as its own last action, which is exactly the silent-dark
failure mode above; (2) accumulating cards on the board is not a bug — it is
board history, and it is what made a 13-day Splunk outage visible in the first
place. `hermes kanban gc` handles archived-card retention on its own schedule.

Every card is additionally **capability-gated**: docs-sync requires the GitHub
App private key plus the Slack bot/app tokens and home channel — a false gate
means no crontab entry, never an error.

Card output goes to `hermes_agent_slack_hermes_all_channel` (`#hermes-all`) —
hardcoded in the card body template, since there is exactly one card left and
no per-card channel override to plumb through.

### Script crons (`--no-agent --script`)

These carry no LLM in their fact path, which is why the digests among them
survived the fabrication incidents: the script gathers the numbers and its
stdout is delivered verbatim.

| Cron | Schedule (UTC) | Script | Delivery |
| --- | --- | --- | --- |
| `kanban-docs-sync` | `13 8 * * 1` | `hermes kanban create` (native, no script) | none — creates the card |
| `splunk-status-digest` | `52 7-23 * * *` | `splunk-digest.py` | `slack:<hermes-all>` |
| `kanban-digest` | `*/15 * * * *` | `kanban-digest.py` | `slack:<digest>` |
| `splunk-error-digest` | `37 * * * *` | `splunk-error-digest.py` | `slack:<digest>` |
| `splunk-security-digest` | `22 */6 * * *` | `splunk-security-digest.py` | `slack:<digest>` |
| `zammad-auto-close` | `17 5 * * *` | `zammad-auto-close.py` | `slack:<hermes-all>` — **off by default** |

`splunk-status-digest` runs on waking hours only, and a fully quiet run goes
`[SILENT]` unless `HEARTBEAT_HOURS` (module constant, currently 6) has elapsed
since the last real post. A CRITICAL finding is exempt and posts every run.
The older "hourly heartbeat, never `[SILENT]`" law is **superseded** — see the
`hermes_agent` role README for both decisions.

`kanban-digest` is the master board report: it reads `kanban.db` read-only and
says what every card did since its own previous run. It is deliberately
excluded from `hermes_agent_seeded_cron_names` so it survives a cluster pause
window — it is what tells you the board is wedged. A run with **nothing** to
report (no completion, failure, retry or overrun) goes `[SILENT]` until
`hermes_agent_kanban_digest_heartbeat_hours` (default 6) has elapsed since the
last delivered post — the same gate `splunk-status-digest` carries. Real board
activity is never gated by it.

`splunk-error-digest` and `splunk-security-digest` cluster events by **error
signature** — the raw line with timestamps, pids, IPs, hex ids and numbers
normalised out — not by host/sourcetype volume. One fault on thirty machines is
one line naming its blast radius, rather than thirty lines of counters.

> **The MCP drops `earliest`.** `splunk_run_query`'s `earliest` argument is
> ignored by the Splunk MCP server: the same query returns byte-identical
> results for `-1h`, `-24h` and `-7d` (verified live 2026-07-28). The triage
> digests therefore write their window **inline in the SPL**, where Splunk
> applies it. Any new job that relies on the argument alone will silently report
> a ~24h figure under whatever heading it claims.

### Agentic direct-deliver digest crons

`hermes_agent_direct_cron_jobs`, reconciled by `tasks/reconcile_direct_cron.yml`.
Each is *agentic* — the gateway runs the prompt and delivers the model's final
response straight to Slack — with prompt bodies pulled from the pinned
`ai-llm-prompts` catalog (`prompt_file`). The Slack channel is never hardcoded:
it comes from `HERMES_SLACK_DIGEST_CHANNEL`, and the whole reconcile loop is
skipped (with a loud warning) when that is unset.

**Three of the nine are enabled** (was four; `splunk-parsing-quality-v2`
retired 2026-08-01). The rest are retired or superseded, and are explicitly
`cron pause`d by `tasks/main.yml` — declaring `enabled: false` alone does
*not* stop a job already running on the guest (see
`tests/hermes_agent/test_retired_direct_crons.py`, which makes that pairing
mandatory rather than remembered).

| Job | Schedule (UTC) | Enabled | Note |
| --- | --- | --- | --- |
| `zammad-incident-review-v2` | `9 13 * * *` | yes | |
| `github-org-triage-v2` | `26 8 * * *` | yes | |
| `daily-operator-summary-v2` | `31 12 * * *` | yes | |
| `splunk-parsing-quality-v2` | `17 3 * * *` | no | 2026-08-01: replaced by `splunk-parsing` card (stale `index=network`, no dedup) |
| `splunk-security-lens-v2` | `22 */6 * * *` | no | superseded by `splunk-security-digest` |
| `splunk-error-triage-v2` | `37 * * * *` | no | superseded by `splunk-error-digest` |
| `anomaly-hunt-v2` | `41 6,18 * * *` | no | retired, no replacement |
| `homelab-ai-fabric-status-v2` | `3 */6 * * *` | no | replaced by the card of the same name |
| `splunk-hourly-digest-v3` | `52 * * * *` | no | superseded by `splunk-status-digest` |

`zammad-incident-review-v2` and `github-org-triage-v2` are intentionally kept
as crude-but-honest daily interim coverage: their kanban twins
(`zammad-review` every 2h, `github-triage` every 6h) are richer but paused
under the throttle, and unpausing either would multiply that topic's enqueue
rate 4-12x — a throughput increase, not the 1-for-1 swap `splunk-parsing` got.
Retire these two in favour of their kanban twins in the SAME change that
unpauses them, not before, so the topic is never dropped in between.

### Systemd units (not crons)

`hermes-gateway.service`, `hermes-dashboard.service`,
`hermes-brain-watchdog.timer` (+ its alert service), the optional
`hermes-brain-sync.timer`, and the optional `hermes-vikunja-bridge.service`
(off by default — see the role README).

Drift recovery: when the brain model changes, only *drifted* seeded jobs are
removed and re-seeded (`tasks/main.yml`); the canonical set is
`hermes_agent_seeded_cron_names`.

> **Cron names must not be substrings of one another.** The reconcile's
> existence test is a substring match against `cron list --all` — which is why
> the script digest is `splunk-error-digest` and not `splunk-error-triage`.

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
| `GH_PAT_WRITE_PROJECT_ISSUES` | `github-triage` card + github-issues skill | empty until the token is issued |
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
the `splunk-*` direct-cron jobs not being created — their capability gate in
`hermes_agent_direct_cron_jobs` is false.

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
entry in the repo-root `llm-models.yml` registry carrying
`repetition_penalty: 1.05` in `extra_body`; because the router serves that real
id (no alias indirection),
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

## Cron schedule — decisions taken

This section used to propose cadence changes. They have since been decided and
applied; it now records what was settled and why, so the same ground is not
re-litigated.

- **Throughput throttle (2026-07-24, Zammad #17143).** The fleet was heavier
  than the single shared serving deployment could carry. 16 of 18 cards were
  paused via `hermes_agent_kanban_paused_jobs`, leaving `splunk-triage` and
  `homelab-ai-fabric-status` plus the script-fed digests. Lift it one card at a
  time, least costly first, once capacity is proven.
- **The LLM `splunk-digest` card is removed (2026-08-01; was "retired" —
  paused — since 2026-07-24).** It was replaced by the script-fed
  `splunk-status-digest`, whose fact path contains no model at all — the fix
  for the fabricated "33 indexes / no anomalies" reports and for the blind
  spot that masked a ~10.5h ingestion outage. Leaving it merely paused turned
  out to be its own trap: `splunk-triage`'s prompt recalled a memory key only
  this card's worker wrote, so once the worker stopped running that recall
  silently always found nothing — see the note under "Kanban cards" above.
- **2026-08-01 kanban audit: one 1-for-1 swap, one new card.**
  `splunk-parsing-quality-v2` (direct cron) is retired in favour of the
  `splunk-parsing` kanban card — same daily cadence, so no throughput
  increase, and its fixed SPL was proven wrong (queried the stale
  `index=network`). The new `fleet-health` card fills the one gap the audit
  found no existing card covers: something watching Hermes' own reliability
  trend, not a downstream system. Both changes and the full per-card
  KEEP/MERGE/DELETE/NEW rationale are in the PR that introduced them.
  **Superseded by the native-cron reframe below** — every card this audit
  paused or swapped is now a plain direct-cron job, and the throughput
  throttle it describes no longer exists as a mechanism.
- **Native-cron reframe: 17 of 18 cards become plain cron jobs, one stays
  Kanban.** The per-card enqueuer script depended on the model running
  `hermes kanban archive` as its own last action to free its idempotency key
  — skip that step once and the job goes silently dark forever, with no
  atomic native alternative. The real fix was the framing, not the archive
  step: a recurring report is a scheduled broadcast, not a discrete tracked
  unit of work, so it belongs on `hermes_agent_direct_cron_jobs`
  (`tasks/reconcile_direct_cron.yml`, already existed for the `-v2` jobs) with
  its recall/save memory pattern carried over verbatim. `docs-sync` is the one
  exception — genuine discrete work a human expects tracked to completion —
  and gets a per-run (not stable) idempotency key instead, because
  accumulating cards on the board is history, not a bug: it is what made a
  13-day Splunk outage visible. See "Recurring reports are plain cron jobs"
  above.
- **The "never `[SILENT]`" heartbeat law is superseded (2026-07-26).** 38 of 40
  runs in one UTC day carried zero information. A quiet run now stays silent
  unless `HEARTBEAT_HOURS` (6) has elapsed; a CRITICAL finding is exempt and
  posts every run.
- **Waking hours (2026-07-26).** `splunk-status-digest` runs `52 7-23 * * *`.
  Overnight posts were read the next morning anyway. Urgent alerting is the
  silent-unless-anomaly `splunk-triage` path, not the digest.
- **Staggering is already applied.** Minutes are spread across the fleet
  precisely so two long runs do not hit the one resident brain together; keep
  it that way when adding a job.
- **Self-directed work exists.** `splunk-deepdive` (quiet RAG, no alert) and
  the self-perpetuating `review` card cover it. Both are currently paused under
  the throttle rather than removed.

---

[docs.jacobpevans.com](https://docs.jacobpevans.com)
