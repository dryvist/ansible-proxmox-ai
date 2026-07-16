# Curriculum job: orient — verified self-orientation

You are running as a one-off submitted job (not a cron). This is a graded
orientation exercise: produce a VERIFIED map of your own operating environment.
Every claim you make must be backed by a tool call or command you actually ran
in this session — no answers from memory alone. Memory may tell you where to
look; only a fresh probe makes a claim reportable.

## Objective

Build an accurate, evidence-backed "who am I, what can I reach, what is my
current state" report.

## Scope — verify each of these, in order

1. **Brain**: which model and base URL you are configured to use (you know this
   from your own config context); confirm the brain is responsive (you are
   thinking, so cite that plus one trivial tool round-trip).
2. **Memory**: recall one existing baseline or fact from memory, then write one
   new memory entry recording this orientation run. Report whether both
   operations succeeded.
3. **Splunk MCP**: run ONE bounded query (e.g. `| tstats count where index=*
   by index` over the last hour) and report the index list you got back.
4. **Context7 MCP**: resolve one library id (any well-known library) and report
   success/failure.
5. **Wiki**: list your wiki's top-level areas and how many pages exist in the
   `splunk` area.
6. **Cron fleet**: list every cron job currently registered (name, schedule,
   paused/active), and note any job that failed in its most recent run.
7. **Slack**: confirm you can deliver by posting the final summary (step below).
8. **Gaps**: name each tool you are configured for but could NOT successfully
   use this session (e.g. Codex escalation not yet bootstrapped, GitHub PAT
   empty, Zammad not deployed), with the exact error observed.

## Deliverable

- Wiki page `curriculum/orient` (create or overwrite): a table with one
  row per item above — item | claim | evidence (the tool/query used and its
  key result) | status (OK / DEGRADED / UNAVAILABLE).
- Then post to your Slack home channel: a compact summary (≤15 lines) — counts
  of OK/DEGRADED/UNAVAILABLE, the cron-fleet state, and any surprises.

## Success criteria

- Every row has real evidence from THIS session; zero unverified claims.
- The gaps section is honest — a tool you couldn't reach is reported as such,
  never papered over.
- Wiki page exists and the Slack post is delivered.

## Budget (hard)

At most 30 agent turns and about 45 minutes. If you hit the budget, stop,
publish whatever rows are complete, and mark the rest `NOT-CHECKED (budget)`.
Never retry a failing tool more than twice.
