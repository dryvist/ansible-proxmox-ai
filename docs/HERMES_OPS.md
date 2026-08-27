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
runs, so anything a job needs to remember it must write to memory. That
property held under the Kanban-board design (#83) and holds equally under the
native-cron design described below: neither carries state in-process.

Split into focused pages, each covering one concern:

- [Cron fleet](hermes-ops/cron-fleet.md) — the recurring report/script/agentic
  cron jobs, the native-cron reframe, and the systemd units alongside them.
- [Memory](hermes-ops/memory.md) — the Hindsight provider, persistence, and
  the shared-across-profiles scoping.
- [Serving self-heal](hermes-ops/serving-self-heal.md) — the zombie watchdog
  that recovers a wedged serving host.
- [Repetition guard](hermes-ops/repetition-guard.md) — the tuned
  `repetition_penalty` entry that stops tool-call loops.
- [Operating profiles](hermes-ops/operating-profiles.md) — the manual
  verification runbook: smoke test, memory-scope check, add/remove a profile,
  concurrency.
- [Cron schedule — decisions taken](hermes-ops/cron-schedule-decisions.md) —
  the settled history of cadence and throughput changes, so the same ground
  is not re-litigated.

---

[docs.jacobpevans.com](https://docs.jacobpevans.com)
