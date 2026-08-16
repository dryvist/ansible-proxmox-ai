# Recurring work is all plain cron now — Kanban is ad-hoc/follow-up only

Every recurring workload — the Splunk fleet above, `github-triage`,
`daily-summary`, `zammad-review`, `homelab-ai-fabric-status`, the nightly wiki
pass, the 8h `review`, and (as of this reframe) `docs-sync` — is a
**direct-deliver cron job** (`hermes_agent_direct_cron_jobs`,
`tasks/reconcile_direct_cron.yml`), not a Kanban card and not an agentic cron
session. Each fires an isolated LLM run on its own schedule and delivers
straight to Slack (`--deliver`) — no board involved, `hermes_agent_kanban_cards`
no longer exists at all. This replaced an earlier Kanban-card design (#83): a
script-only enqueuer cron created one card per slot for the board's dispatcher
to run in a fresh worker session, which fixed #83's original state-leak
problem (INC-17120) but introduced its own silent-failure hazard — the worker
had to run `hermes kanban archive` as its own last action to free its
idempotency key for the next fire, and a skipped archive (model forgets, a
runtime cap killing the process, any flake) left the job silently dark
forever, since `hermes kanban complete` transitions to `done`, never
`archived`, and no atomic native alternative exists. A per-run (not stable)
idempotency key would have solved that deterministically, but it didn't need
solving: kanban is not for repetitive scheduled work by definition, docs-sync
included — it runs weekly on a fixed schedule. The board keeps doing what it
is actually for: ad-hoc work, and the follow-up cards these cron jobs
themselves file via `kanban_create` (`review`'s gap follow-ups,
`anomaly-hunt`'s findings, `ai-news`'s actionable items).

**Real semantics `hermes cron create` cannot express natively** (verified
against the live CLI's `--help` and, for profile, against `cron/scheduler.py`
itself — not assumed):

- **`assignee`/profile selection** — no `cron create` flag exists. Restored
  via a per-job `hermes_home:` override (7 of the 18 converted jobs had a real
  profile: `daily-status`, `zammad-review` → `homelab-admin`; `splunk-triage`,
  `splunk-security`, `splunk-parsing`, `splunk-deepdive`, `anomaly-hunt` →
  `splunk-admin`) — but a HERMES_HOME override alone is not sufficient: cron
  jobs run IN-PROCESS inside whichever gateway registered them, and only the
  default profile's gateway (`hermes-gateway.service`) is persistent. A named
  profile needs its own trigger — `hermes cron tick` ("run due jobs once and
  exit"), fired every 5 minutes per profile
  (`hermes_agent_profile_cron_tick_timeout`, `tasks/main.yml`).
- **`max_runtime`** — no `cron create` flag either. Partially restored for the
  7 profile-scoped jobs only: `timeout <duration>` wraps their tick-trigger
  invocation, an approximate per-TICK ceiling (more than one due job can share
  a 5-minute window), not a strict per-job one. The other 11 run inside the
  default gateway's in-process ticker, which has no external invocation point
  to wrap at all — no runtime cap exists for them, and none can be added
  without a persistent gateway process per job.
- **`max_retries`** — no `cron create` equivalent. Not restored; an accepted,
  documented loss.
- **Outcome-based delivery split** (`channel_when_healthy` /
  `terse_when_healthy`, one job: `homelab-ai-fabric-status`) — `--deliver`
  takes exactly one fixed target. Restored as **prompt text**: the shared
  reporting footer instructs the model to self-route via the terminal command
  `hermes send` when the run is a genuine all-clear, ending with `[SILENT]` so
  `--deliver` does not also post it.

None of these were silently dropped.

**Every direct-cron job posts a full report, not a sentence**, and every one
carries the anti-fabrication evidence contract — both restored as a shared
Jinja prompt-text footer (`templates/direct-cron-footer.md.j2`, appended to
every job's prompt in `reconcile_direct_cron.yml`), the same distinction that
made the original enqueuer-footer design acceptable: data, not a script.
check that guarantees new prompt text keeps doing so.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_direct_cron_jobs` | — | the plain-cron job table (name, schedule, prompt var, skill, deliver target) — every recurring workload, 27 entries |
| `hermes_agent_slack_hermes_all_channel` | firehose channel id | default delivery channel for jobs' completion **report** |
| `hermes_agent_superseded_kanban_enqueuer_cron_names` | — | the retired per-card `<job>-enqueue` crons + the old safety net, removed at converge |

## Master board digest (`kanban-digest`)

One report covering **everything the board did since this digest last ran** —
cards completed (with the worker's own summary, not just a title), cards that
failed, retried or **exited open** (the run ended and the card never reached a
settled column), and cards still running past their own `max_runtime`. It is the
report that makes per-workload digest crons redundant.

`--no-agent --script`, same contract as the script-fed Splunk digests: the script
reads `kanban.db` **read-only** (`mode=ro`) and its stdout is delivered verbatim.
No LLM and no network in the fact path, which is the point — this is the surface
that announces a wedged board, and a wedged board is usually a wedged brain. For
the same reason it is deliberately **absent from
`hermes_agent_seeded_cron_names`** so brain-fleet reconciliation cannot pause
the digest that reports a wedged board.

`hermes kanban` has no "every run that ended since T" query — `list --json`
carries task rows whose `result` column is null, and per-attempt outcome and
summary live in `runs --json <task_id>`, one task id at a time. One read-only SQL
query over `task_runs` replaces a per-card subprocess fan-out.

"Since the previous run" is a schema-versioned state file beside the Splunk
digests' state. Missing or corrupt degrades to one scheduling interval and
**says so** in the post; a broken read is delivered as an explicit `FAILED` line,
never as silence (an empty post would read as a healthy board).

**Quiet runs are heartbeat-gated** (operator decision, 2026-07-28). A run with
no completion, failure, retry or overrun to report goes `[SILENT]` unless
`hermes_agent_kanban_digest_heartbeat_hours` has elapsed since the last
*delivered* post. At the 15-minute cadence the quiet branch was firing ~90 times
a day with byte-identical board counts, which is the noise this removes; the
rule is **never post a message whose entire content is "nothing happened"**
unless the heartbeat interval has passed. Two invariants make that safe:

- **Real activity is never gated.** The check runs only on the fully-quiet
  branch, so a failure, an overrun or a completion posts immediately, every run.
- **Unknown or future-dated last-post counts as DUE.** Erring towards posting is
  the only safe direction — a suppressed heartbeat is indistinguishable from a
  dead cron, which is exactly what this digest exists to announce.

A suppressed run still **advances its window** (it did cover that window) but
does **not** advance `last_post_epoch`, or every quiet run would reset its own
heartbeat clock and the heartbeat would never fire.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_kanban_digest_interval_minutes` | `15` | the only place the cadence is written; schedule and fallback derive from it. Steady state hourly |
| `hermes_agent_kanban_digest_heartbeat_hours` | `6` | quiet-post ceiling; `0` restores "post every run" |
| `hermes_agent_kanban_digest_cron_schedule` | derived | never set by hand |
| `hermes_agent_kanban_digest_channel` | `hermes_agent_digest_slack_channel` | delivery surface; never a literal id |
| `hermes_agent_kanban_digest_enabled` | derived | Slack bot + app tokens + channel set. No Splunk or brain dependency |

## Script-fed Splunk triage digests (`hermes_agent_triage_jobs`)

One template (`templates/splunk-triage.py.j2`) rendered once per job; adding a
job is config, not code. Both jobs report **error signatures**, not host volume.

Before (what the operator actually saw): a leaderboard of counters —
`openbao-02 / syslog — 18.0k events (was 17.9k)`. After: the fault leads, the
blast radius follows —

```text
:rotating_light: *NEW* `bao[<pid>]: <ts> [ERROR] storage.raft: failed to heartbeat to: peer=<ip>:<n> ... no route to host`
    3.5k events in the 1h window, absent from the previous run · openbao-02
`systemd[<pid>]: Failed to mount tmp.mount - Temporary Directory /tmp.`
    78 events (was 78) · 9 hosts: openbao-02, openbao-01, openbao-21 (+6 more)
```

Signatures are produced in Splunk by an ordered `rex mode=sed` chain that
normalises timestamps, pids, IPs, hex ids and finally bare digits. **The order
is load-bearing**: put the catch-all digit rule before the timestamp rule and an
ISO timestamp becomes `<n>-<n>-<n>T<n>:<n>:<n>`, splitting one fault across as
many signatures as it has distinct timestamps. A test pins the ordering.

Under-normalising is the safe direction and over-normalising is not: an
unrecognised varying token splits one fault into several signatures (visible and
fixable), while an over-broad rule merges genuinely different faults into one
(invisible, and it hides the thing you needed).

> **The MCP drops `earliest`.** Proven live 2026-07-28: `splunk_run_query`
> returns byte-identical results for `earliest=-1h`, `-24h` and `-7d`. Every
> hourly digest ever posted was a ~24h figure under a "last 1h" heading — which
> is how one host read as 18k errors/hour when its real hourly rate was 36, and
> was not even the estate's top error source. The window is therefore written
> **inline in the SPL**; the argument is still passed so the job stays correct
> if the server is ever fixed. `splunk-status-digest` passes `-24h`, which
> happens to match the server's default, so it is accidentally rather than
> deliberately correct — fix it there too when the MCP is touched.
