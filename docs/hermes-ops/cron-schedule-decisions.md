# Cron schedule — decisions taken

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
- **Native-cron reframe: 18 of 18 cards become plain cron jobs, none stay
  Kanban.** The per-card enqueuer script depended on the model running
  `hermes kanban archive` as its own last action to free its idempotency key
  — skip that step once and the job goes silently dark forever, with no
  atomic native alternative. A per-run (not stable) idempotency key would have
  solved that deterministically, and `docs-sync` briefly shipped with exactly
  that as the one card kept on the board. It didn't need solving: Kanban is
  not for repetitive scheduled work by construction, and docs-sync runs weekly
  on a fixed schedule — cron by definition. Every one of the 18, including
  docs-sync, is now a `hermes_agent_direct_cron_jobs` entry
  (`tasks/reconcile_direct_cron.yml`, already existed for the `-v2` jobs) with
  its recall/save memory pattern carried over verbatim. Kanban keeps doing
  what it is actually for: ad-hoc work, and the follow-up cards these cron
  jobs file via `kanban_create`. See "Recurring reports are plain cron jobs"
  above for what `hermes cron create` cannot express natively (profile,
  runtime cap, outcome-split delivery) and how each is restored or accepted
  as a documented loss.
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
