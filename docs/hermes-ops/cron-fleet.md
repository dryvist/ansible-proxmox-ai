# Cron fleet

Schedules are UTC (`hermes_agent_timezone: UTC`). Everything below is defined
in `roles/hermes_agent/defaults/main.yml` — this doc restates it and can drift,
so **the defaults file wins on any disagreement**.

## Recurring reports are plain cron jobs, not Kanban cards (native-cron reframe)

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

All 18 pre-reframe cards, docs-sync included, are now direct-cron jobs — see
"Real semantics `hermes cron create` cannot express" below for what that cost:

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
| `docs-sync` | `13 8 * * 1` (weekly) | `#hermes-all` |
| `self-audit` | `29 3,15 * * *` | `#hermes-issues` |

Every job is additionally **capability-gated**: all require the Slack bot
token, app token and home channel; the `splunk-*` jobs also require
`hermes_agent_splunk_monitor_enabled` and the Splunk MCP URL. A job whose gate
is false is never created — the role runs inert, never errors.

`homelab-ai-fabric-status` splits its report by outcome (all-clear to the
noise channel, a break to issues) — restored as **prompt text**, not a
`--deliver` flag: `--deliver` (`#hermes-issues`, the default/breaking-run
destination) takes exactly one fixed target, so the shared reporting footer
(`templates/direct-cron-footer.md.j2`, appended to every direct-cron job's
prompt) instructs the model to self-route to the noise channel via the
terminal command `hermes send` when the run is a genuine all-clear, ending
with `[SILENT]` so `--deliver` does not ALSO post it. `terse_when_healthy`
(one-line "All systems operational" on that branch) is restored the same way.

`docs-sync` was initially kept as the one surviving Kanban card, with a
**per-run** (not stable) idempotency key to solve the enqueuer's archive
problem deterministically. That solution was correct but unnecessary: the
operator's rule settles it upstream — Kanban is not for repetitive scheduled
work, and docs-sync runs weekly on a fixed schedule, so it is cron by
definition. `hermes_agent_kanban_cards` no longer exists at all; the board
keeps doing what it is actually for — ad-hoc work, and the follow-up cards
these cron jobs file via `kanban_create` (`review`'s gap follow-ups,
`anomaly-hunt`'s findings, `ai-news`'s actionable items).

**Real semantics `hermes cron create` cannot express**, verified against the
live CLI's own `--help` (not assumed) and, for the profile finding, against
`cron/scheduler.py` itself: profile/`assignee` selection — no `cron create`
flag exists, so the 7 jobs that had a real profile (2 → `homelab-admin`:
`daily-status`, `zammad-review`; 5 → `splunk-admin`: `splunk-triage`,
`splunk-security`, `splunk-parsing`, `splunk-deepdive`, `anomaly-hunt`) get a
per-job `HERMES_HOME` override instead (`hermes_home:` on the item,
`reconcile_direct_cron.yml`). That alone is not sufficient: `hermes cron`'s
ticker runs IN-PROCESS inside whichever gateway registered the job — only the
default profile's gateway (`hermes-gateway.service`) runs persistently, so a
job registered under a named profile needs its own trigger. `hermes cron
tick` ("run due jobs once and exit") is the native one; a `*/5 * * * *`
crontab entry per profile (`hermes_agent_profile_cron_tick_timeout`) fires it,
which provides an additional outer ceiling for those 7 (`timeout <duration>`
wraps the whole tick invocation — a per-tick cap, not strictly per-job, since
more than one due job can land in the same 5-minute window). Every agentic cron
conversation, including the default gateway's in-process jobs, is separately
bounded by the role's monotonic aggregate wall clock. That is independent of
upstream `HERMES_CRON_TIMEOUT`, which resets on API/tool/stream activity.
Script-only jobs retain their separate native script timeout.
`max_retries` (the failure-limit circuit breaker) has no `cron create`
equivalent either and is not restored — an accepted, documented loss.

`tasks/main.yml` actively *removes* the superseded per-card enqueuer crons
(`hermes_agent_superseded_kanban_enqueuer_cron_names`, the old `<job>-enqueue`
names plus `kanban-enqueue-safety-net`) and *pauses* (not deletes) the three
`-v2` direct-cron jobs the reframe replaces 1-for-1
(`zammad-incident-review-v2`, `github-org-triage-v2`, `daily-operator-summary-v2`),
so a guest converged mid-migration does not double-fire.

## Script crons (`--no-agent --script`)

These carry no LLM in their fact path, which is why the digests among them
survived the fabrication incidents: the script gathers the numbers and its
stdout is delivered verbatim.

| Cron | Schedule (UTC) | Script | Delivery |
| --- | --- | --- | --- |
| `splunk-status-digest` | `52 7-23 * * *` | `splunk-digest.py` | `slack:<hermes-all>` |
| `kanban-digest` | `9 * * * *` | `kanban-digest.py` | `slack:<digest>` |
| `splunk-error-digest` | `37 * * * *` | `splunk-error-digest.py` | `slack:<digest>` |
| `splunk-security-digest` | `22 */6 * * *` | `splunk-security-digest.py` | `slack:<digest>` |
| `zammad-auto-close` | `17 5 * * *` | `zammad-auto-close.py` | `slack:<hermes-all>` — **off by default** |
| `cron-failure-rollup` | `7 * * * *` | `cron-failure-rollup.py` | `slack:<issues>` |

`splunk-status-digest` runs on waking hours only, and a fully quiet run goes
`[SILENT]` unless `HEARTBEAT_HOURS` (module constant, currently 6) has elapsed
since the last real post. A CRITICAL finding is exempt and posts every run.
The older "hourly heartbeat, never `[SILENT]`" law is **superseded** — see the
`hermes_agent` role README for both decisions.

`cron-failure-rollup` reads every store's `jobs.json` and posts one message
naming each job whose last run failed, grouped by cause (wall-clock, budget,
auth, upstream-5xx, ...). It reposts only when the failing set changes or
`hermes_agent_cron_failure_rollup_heartbeat_hours` (default 6) has elapsed,
and posts one all-clear when the set empties.

`kanban-digest` is the master board report: it reads `kanban.db` read-only and
says what every card did since its own previous run. It is deliberately
excluded from `hermes_agent_seeded_cron_names` because it is what tells you the
board is wedged. It ticks hourly (`hermes_agent_kanban_digest_interval_minutes`,
60). A run with **nothing** to report (no completion, failure, retry or
overrun) goes `[SILENT]` until `hermes_agent_kanban_digest_heartbeat_hours`
(default 24) has elapsed since the last delivered post — the same gate
`splunk-status-digest` carries. Real board activity is never gated by it.
Every delivered post carries a stuck line — cards unsettled for more than 48
hours, by status, with the age of the oldest — so a blocked pile is read as
"44 blocked, oldest 9d" rather than a count that never changes. The
stalled-board alarm follows an escalate-then-quiet ladder: it posts when the
streak reaches its threshold, at three times it, then once a day, and posts
one all-clear when the board drains again.

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

## Agentic direct-deliver digest crons

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

## Systemd units (not crons)

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
