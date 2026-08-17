# Curriculum (graded five-job eval, versioned)

`files/curriculum/` owns the manifest, grading, and submission workflow for
the repeatable job set that measures whether the agent is actually useful.
Prompt bodies come from the pinned `ai-llm-prompts` catalog. The complete
curriculum is deployed to `$HERMES_HOME/curriculum/` on every converge.

| Artifact | Role |
| --- | --- |
| `curriculum.yml` | Canonical manifest: order, budgets, expected skills, and each job's **machine-checkable `success_checks`** |
| `jobs/*.md` | Five catalog-backed prompts, materialized during converge and submitted verbatim as `POST /v1/runs` input |
| `grading-sheet.md` | Four 0-3 dimensions per job + verified-claim spot checks + the cross-job omissions check |
| `submission-runbook.md` | Turnkey submission: preflight gates, key fetch, staggered submits, collection, grading |

The jobs: `orient` (verified self-orientation), `reposweep` (read-only
GitHub triage), `splunk` (one deep investigation via the bundled
splunk-monitor skill), `apps` (fleet health: log errors cross-referenced
with repo issues; files capped `[hermes-fleet-health]` issues through the
agent's own PAT flow), `improve` (evidence-based self-improvement; files
capped `[hermes-improve]` issues). `success_checks` are evaluated from the
run object, event stream, and GitHub — never the job's own summary.

Layer-1 asserts guarantee the manifest is always executable: unique job ids,
every `prompt_file` mapped to an immutable catalog artifact, and a non-empty
`success_checks` list per job.

## Runner-enforced tool policy (per platform)

A submitted `input` — and everything a job retrieves while running — is
untrusted text that can carry prompt injection. The **runner's toolset
resolution**, not the prompt, decides what each platform may load; injected
instructions cannot widen a toolset list the runner never registered. Policy
is plain data in `defaults/main.yml`:

| Layer | Rendered as | Scope |
| --- | --- | --- |
| `hermes_agent_disabled_toolsets` | `agent.disabled_toolsets` | Global deny floor; no allowlist can widen past it |
| `hermes_agent_api_server_toolsets` | `platform_toolsets.api_server` | API-submitted runs (untrusted input) |
| `hermes_agent_cron_toolsets` | `platform_toolsets.cron` | The scheduled fleet (upstream also hard-blocks cronjob/messaging/clarify in cron) |
| `hermes_agent_slack_toolsets` | `platform_toolsets.slack` | The interactive, allowed-users-gated surface |

The allowlists deliberately exclude `cronjob` (no injected persistence) and
`browser`/`delegation` (widest attack surface / cost amplification) from
every platform — the risk is the capability, not the trust level of whoever
triggered it. `api_server` and `cron` additionally exclude `clarify`
(headless: no one to answer); `slack` allows it, since that rationale doesn't
hold for an interactive human-in-the-loop surface. Layer-1 asserts fail the
converge if any excluded toolset creeps back into an allowlist, if a denied
toolset is simultaneously allowlisted, or if any allowlist goes empty.
Enabled MCP servers (splunk/context7/codex) layer onto the allowlists by
upstream's platform-tools semantics. `hermes_agent_slack_toolsets` renders
only when the Slack bot token is set, matching the other Slack-gated config.

## Brain-health watchdog (no cron-failure spam)

The cron fleet above talks to a **single-deployment brain** (the real model id in
`ai_default_model`, served by one Mac Studio via the `llm_router` proxy) with
**no viable fallback**. When
that brain is unreachable, two upstream facts combine badly: each cron run is a
**fresh, stateless session**, and upstream *always* delivers a failure —
*"Failed jobs always deliver regardless of the `[SILENT]` marker; only successful
runs can be silenced."* So a brain outage makes every seeded job fail and DM the
operator (twice an hour for `splunk-security` alone), while nothing pages that the
brain is even down — `service_deadman` watches DNS/Traefik/HAProxy/OpenBao, not
the LLM fabric.

This watchdog closes both gaps with a small `systemd` timer
(`hermes-brain-watchdog.timer`, every 60s, run as the `hermes` user):

1. **Probe** the default brain end-to-end through the same router URL the crons use — a
   1-token completion, so it catches a connection error *and* a reachable-but-
   wedged brain. It hits the already-active model (no cold-model spawn) and keeps
   it warm, matching the intended 24/7 posture.
2. **Debounce** — declare DOWN only after `down_after` consecutive failures (3 ≈
   3 min) and UP after `up_after` successes (2). This rides brief bounces
   (rotation flips, cold reloads) so the watchdog never becomes a *new* source of
   spam.
3. **On a transition** — `hermes cron pause` (or `resume`) the brain-dependent
   fleet (`hermes_agent_brain_dependent_cron_names`; user/agent jobs are never
   touched), confirm each job's new state by
   reading `hermes cron list --all` back rather than trusting the command's exit
   code, and alert
   **exactly once** per edge to **both** a Slack DM (the operator, same place the
   spam was) and an **urgent ntfy** push (the `keystone` feed other homelab
   outages page on). Paused jobs don't fire, so the outage stops producing spam
   instead of amplifying it.
4. **Flap coalescing** — debounce alone doesn't stop a genuinely *unstable*
   backend from alerting on every edge (confirmed live: a 31h-unstable backend
   produced dozens of up/down DM pairs). Completing the first down/up cycle
   opens a `flap_cooldown_seconds` cooldown window; any further edge inside it
   is coalesced (counted, window extended) instead of alerting. Once the
   window finally elapses with the brain stable, one summary reports the whole
   episode ("unstable since X, N flaps, stabilized at Y"); a clean single
   cycle with nothing coalesced clears silently since its two normal alerts
   already told the story.

Pausing loses no coverage a run would otherwise achieve — the brain is down either
way — it just makes the gap visible **once** instead of drowning it in 500s.
Gated on the same Slack tokens that seed the fleet (no fleet → nothing to guard).

Cron-failure delivery text itself (raw exception strings like a mid-stream
fallback error or "no available server") comes from Hermes Agent's own
always-deliver cron failure path — upstream, not rendered by this role — and
offers no config hook to translate or filter it (verified against the pinned
version's docs: error tracebacks are explicitly never touched by any
user-facing translation setting). This watchdog's pause/resume already
suppresses that spam for a full brain outage; a single transient error on an
otherwise-healthy brain can still deliver its raw text once. Tracked as an
upstream ask, not fixable from this role without inventing a delivery-layer
proxy this repo doesn't otherwise need.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_brain_watchdog_enabled` | `true` | Deploy + start the watchdog timer |
| `hermes_agent_brain_watchdog_interval` | `60s` | Probe cadence (`OnUnitActiveSec`) |
| `hermes_agent_brain_watchdog_probe_timeout` | `15` | Per-probe curl deadline (seconds) |
| `hermes_agent_brain_watchdog_down_after` | `3` | Consecutive fails → pause + alert |
| `hermes_agent_brain_watchdog_up_after` | `2` | Consecutive oks → resume + alert |
| `hermes_agent_brain_watchdog_flap_cooldown_seconds` | `3600` | Post-cycle window that coalesces further edges into one summary |
| `hermes_agent_brain_watchdog_ntfy_topic` | `keystone` | ntfy topic for the urgent page |
| `hermes_agent_brain_watchdog_healthcheck_url` | `''` (env `DEADMAN_HC_URL_HERMES_BRAIN`) | External deadman OK-ping target; empty = ping skipped |

## Telling a watchdog pause from a human pause

`hermes cron list --all` shows a job as paused either way — it does not record
*who* paused it. Two things distinguish the two cases without adding any new
state:

- **The watchdog's own state file**, `$HERMES_HOME/brain-watchdog/state`
  (`up` or `down`). If it reads `down`, the watchdog itself paused the seeded
  fleet on a debounced probe failure. If it reads `up` while jobs are still
  paused, the watchdog did not do it — look for a human cause instead
  (`recover-hermes-queue.yml` or a manual `hermes cron pause`).
- **Whether the timer is even running**: `systemctl is-active
  hermes-brain-watchdog.timer`. The queue-recovery playbook explicitly stops the
  timer *before* pausing anything by hand, precisely so the watchdog cannot
  race a deliberate pause or auto-resume mid-maintenance. An inactive timer
  during a paused fleet is conclusive: this is not the watchdog.
- **The audit trail**: every watchdog-driven edge is logged with
  `logger -t hermes-brain-watchdog`, so `journalctl -t hermes-brain-watchdog`
  gives an exact "down at ${time}" / "up at ${time}" history alongside the
  Slack DM + ntfy page it already sent. A human pause instead shows up in the
  Ansible/Terrakube run history for whichever playbook ran.

## Watchdog self-monitoring ("who watches the watchdog")

The watchdog's ntfy + Slack alerts only fire when its *probe* detects the brain
down, and they run **on this LXC** — so a powered-off LXC, a masked timer, or a
wedged systemd silences them with no page. Two mechanisms cover that blind spot,
one external and one same-repo.

**External deadman (the real absence detector).** On every healthy probe the
watchdog pings a healthchecks-style deadman URL (`hc_ping`, from
`hermes_agent_brain_watchdog_healthcheck_url`). The ping stops whenever this host
is gone, its timer is dead, or the brain is unreachable — and "brain unreachable"
includes a silent serving host, since the probe runs the completion end-to-end.
When the pings stop, the external service pages on its own, with no dependency on
anything running here. This mirrors the `service_deadman` convention in the
sibling `ansible-proxmox-apps` repo: the full ping URL comes from the environment
(`DEADMAN_HC_URL_HERMES_BRAIN`), so the check is **provisioned out-of-band** in
the healthchecks instance and its URL exported at converge. Empty URL = no-op, so
the watchdog runs unchanged until the check is provisioned. Provisioning that
external check (and exporting its URL) is the one manual step this does not — and
cannot, read-only — automate.

**Same-repo stopgap (fast crash paging).** A systemd
`OnFailure=` on `hermes-brain-watchdog.service` triggers
`hermes-brain-watchdog-alert.service`, which fires an urgent ntfy push. It
only triggers on a **crashed probe cycle** (an unhandled script error) —
normal brain up/down transitions always `exit 0` and are alerted separately by
the watchdog script itself, so this never doubles up with the alert above.
The alert script runs as root with no dependency on the hermes user or
`.env`, so a broken watchdog environment can't also silence it.

**Fast, but narrow.** The `OnFailure=` stopgap catches only the watchdog
*process* crashing — not the timer being disabled/masked, nor systemd itself
wedging. Those are exactly the cases the external deadman ping above covers
(a masked timer stops pinging, so the check pages), which is why both exist:
the `OnFailure=` unit pages within one cycle on a crash, and the external
deadman backstops every silent-absence case the crash path structurally
cannot see. Repeat alerts are rate-limited by systemd's own `StartLimit*` on
the alert unit (not the watchdog's probe cadence: `StartLimitIntervalSec=1h`,
`StartLimitBurst=1`), so a persistently crashing watchdog pages once per hour
instead of every probe cycle.

The alert script path (`/usr/local/bin/hermes-brain-watchdog-alert.sh`) and
the `StartLimit*` thresholds are literals in
`templates/hermes-brain-watchdog-alert.service.j2` and `tasks/main.yml`, not
role variables — they are operational constants tied 1:1 to this alert unit
that nothing currently overrides per-host.
