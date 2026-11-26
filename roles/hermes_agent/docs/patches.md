# Local Hermes patches

The role installs upstream [Hermes Agent](https://github.com/NousResearch/hermes-agent)
at the pinned release (`hermes_agent_version`) and applies a set of source
patches to the checkout before the gateway starts (`tasks/pinned_patches.yml`,
tag `hermes_patches`), each with readback assertions (`patches_verify*.yml`).
It also ships companion scripts onto the guest.

This page records every patch and shipped script against upstream. Decisions
were checked at the pinned release (v0.23.0) and at the latest upstream
release at the time of the audit (v0.23.4, 2026-11):

- **RETIRE** — upstream supplies the behavior at the pinned release; the
  patch (and its assertions) were removed, and the commit that removed it is
  named in the row.
- **KEEP** — no upstream equivalent at either release; the patch stays.

## `patches_retry_and_markup.yml` — output-cap retry boost ceiling

- Upstream file: `agent/turn_truncation.py`
- Decision: KEEP
- Reason: `boosted_output_cap()` still floors the boost ceiling at `max(32768, anchor * 2)` and does not read the configured `agent.max_tokens` ceiling, at
  v0.23.0 and v0.23.4
- Evidence: [turn_truncation.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/agent/turn_truncation.py)

## `patches_retry_and_markup.yml` — cron delivery tool-call markup guard

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream has no check for unparsed tool-call markup in delivered cron output at either release
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_retry_and_markup.yml` — auxiliary retry backoff base

- Upstream file: `agent/auxiliary_client.py`
- Decision: KEEP
- Reason: the retry count is configurable (`auxiliary.transient_retries`) but the backoff base remains a 1.0 s module constant with no config key at either
  release
- Evidence: [auxiliary_client.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/agent/auxiliary_client.py)

## `patches_retry_and_markup.yml` — 429 through the bounded transient retry

- Upstream file: `agent/auxiliary_client.py`
- Decision: KEEP
- Reason: upstream keeps the bounded same-provider retry deliberately narrow (408/5xx, "Deliberately narrow" in the function's own docstring) and routes rate
  limits to provider rotation
- Evidence: [auxiliary_client.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/agent/auxiliary_client.py)

## `patches_retry_and_markup.yml` — no capacity-error fallback for the explicit local provider

- Upstream file: `agent/auxiliary_client.py`
- Decision: KEEP
- Reason: upstream's fallback gate still treats any capacity error as fallback-eligible for an explicitly configured provider, at both releases
- Evidence: [auxiliary_client.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/agent/auxiliary_client.py)

## `patches_goal_judge.yml` — goal judge timeout

- Upstream file: `hermes_cli/goals.py`
- Decision: RETIRE (commit `refactor(hermes_agent): retire the goal judge timeout patch now covered upstream`)
- Reason: upstream reads the timeout from the `auxiliary.goal_judge.timeout` config key, which its own comment calls the declared default (#91022); present at
  v0.23.0, the pinned release. The role sets the key in the `goal_judge` block of `templates/config.yaml.j2`
- Evidence: [goals.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/hermes_cli/goals.py)

## `patches_goal_judge.yml` — judge transport failure retried without burning turns

- Upstream file: `hermes_cli/goals.py`
- Decision: KEEP
- Reason: upstream stops the goal loop on the first judge transport failure (`if _transport_failed: return _result("stopped", ...)`) at both releases
- Evidence: [goals.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/hermes_cli/goals.py)

## `patches_goal_judge.yml` — judge call timed and its latency logged

- Upstream file: `hermes_cli/goals.py`
- Decision: KEEP
- Reason: upstream logs judge-call failures only; it emits no latency line for the judge path at either release
- Evidence: [goals.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/hermes_cli/goals.py)

## `patches_cron_failure_routing.yml` — self-declared failure routed through the failure lane

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream's `for_failure` lane keys on the runner-reported success flag; a script-fed job that exits 0 and declares its own failure in its output
  produces no upstream signal
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_cron_output_validity.yml` — output-validity guard

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream has no minimum-length or truncation heuristic on successful cron deliveries at either release
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_goal_mode.yml` — idempotent create reconciles goal-mode fields

- Upstream file: `hermes_cli/kanban_db.py`
- Decision: KEEP
- Reason: upstream's idempotency path returns the existing task id without reconciling `goal_mode`/`goal_max_turns`, at both releases
- Evidence: [kanban_db.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/hermes_cli/kanban_db.py)

## `patches_goal_mode.yml` — goal-judge availability probe logs its declines

- Upstream file: `tools/kanban_tools.py`
- Decision: KEEP
- Reason: upstream's `_goal_judge_available()` returns False on lookup failure and on a missing client/model without logging
- Evidence: [kanban_tools.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/tools/kanban_tools.py)

## `patches_worker_reap.yml` — worker reap signals the process group (plus init/self and legacy-row identity guards)

- Upstream file: `hermes_cli/kanban_db_dispatch.py`
- Decision: KEEP
- Reason: upstream's timeout and stale-reclaim reapers signal only the recorded pid (`_sigkill(kill, pid)`) while workers spawn with `start_new_session=True`,
  so a worker's own children survive the reap, at both releases
- Evidence: [kanban_db_dispatch.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/hermes_cli/kanban_db_dispatch.py)

## `patches_run_agent_memory_sync.yml` — external-memory sync silent exits logged

- Upstream file: `run_agent.py`
- Decision: KEEP
- Reason: upstream still ends the sync/prefetch path in a bare `except Exception: pass` at both releases
- Evidence: [run_agent.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/run_agent.py)

## `patches_provider_error_snippet.yml` — provider-error snippet widened 300→500

- Upstream file: `agent/api_error_summary.py`
- Decision: KEEP
- Reason: all five snippet bounds remain `[:300]` at both releases
- Evidence: [api_error_summary.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/agent/api_error_summary.py)

## `patches_cron_failure_artifact.yml` — failed-run artifact hashes the prompt

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream still writes the full prompt into the failed-run header; the success path's `prompt_stamp` carries only a length frame
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_dispatch_tick_log.yml` — per-tick dispatch decision log

- Upstream file: `gateway/kanban_watchers_dispatcher.py`
- Decision: KEEP
- Reason: upstream's `_log_spawn_results` logs only boards that spawned ("Quiet by default: an idle gateway stays silent")
- Evidence: [kanban_watchers_dispatcher.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/gateway/kanban_watchers_dispatcher.py)

## `patches_cron_goal_mode.yml` — opt-in goal mode for native cron jobs

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: no upstream goal-mode path for cron jobs exists at either release
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_cron_memory.yml` — built-in memory store rebound for cron agents

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream constructs cron agents with `skip_memory=False` at the pinned release (the role asserts that upstream line) but does not read the `memory.*`
  config keys or quiet the memory nudge there
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_cron_memory.yml` — creation-time assignee validation

- Upstream file: `hermes_cli/kanban_db.py`
- Decision: KEEP
- Reason: upstream reports profile presence in `known_assignees()` but still accepts creation with an assignee that has no profile on disk
- Evidence: [kanban_db.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/hermes_cli/kanban_db.py)

## `patches_cron_wall_clock.yml` — aggregate cron wall clock (deadline, bounded poll, hard interrupt, classifier branch)

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream's cron watchdog is inactivity-only; the run function's own docstring states "inactivity (not wall-clock)" at both releases, and no aggregate
  deadline or `HERMES_CRON_WALL_TIMEOUT` setting exists
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_cron_wall_clock.yml` — cron session id threaded into the watchdog

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream's watchdog signature carries no session id
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_cron_wall_clock.yml` — fallback exhaustion classified before the provider branches

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream's delivery classifier has no fallback-exhaustion branch
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_cron_wall_clock.yml` — no-chain fallback advice rewritten for router-fronted serving

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream's no-chain advice assumes agent-local fallbacks (`hermes fallback add`); this deployment terminates fallbacks at the router in front of the
  agent
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `patches_cron_wall_clock.yml` — pause suggestion dropped from the repeated-failure nudge

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: upstream still offers pause as the first remedy in the repeated-failure nudge at both releases
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `files/github-route-same-repo.py`

- Upstream file: —
- Decision: KEEP
- Reason: same-repo webhook filter; no upstream counterpart
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

## `files/hermes-gh.py`

- Upstream file: `tools/skills_hub_github.py`
- Decision: KEEP
- Reason: `gh` wrapper with per-call token selection and outbound content rules; upstream's GitHub surface does not provide this
- Evidence: [skills_hub_github.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/tools/skills_hub_github.py)

## `files/hermes-gh-token.py`

- Upstream file: —
- Decision: KEEP
- Reason: identity-scoped GitHub token refresh service; no upstream counterpart
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

## `files/hermes-innovation-log-append.py`

- Upstream file: —
- Decision: KEEP
- Reason: append-only innovation-log helper; no upstream counterpart
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

## `files/hermes-pepper-vault-git-credential`, `templates/hermes-pepper-vault-refresh.sh.j2`

- Upstream file: —
- Decision: KEEP
- Reason: token-minted evidence fetch and a git credential helper on a daily timer; stock helpers persist credentials
- Evidence: `tasks/pepper_vault.yml`

## `templates/cron-failure-rollup.py.j2`

- Upstream file: `cron/scheduler.py`
- Decision: KEEP
- Reason: deterministic hourly failure rollup read from the cron store; upstream tracks failure streaks but ships no rollup digest
- Evidence: [scheduler.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/scheduler.py)

## `templates/hermes-brain-sync.sh.j2`, `templates/hermes-brain-watchdog-alert.sh.j2`

- Upstream file: —
- Decision: RETIRE-CANDIDATE
- Reason: OpenBao brain repoint (alternative: the router alias; disabled by default) and crash page on `OnFailure=` (alternative: the deadman ping)
- Evidence: `tasks/brain_sync.yml` timer, `tasks/brain_watchdog.yml`

## `templates/hermes-brain-watchdog.sh.j2`, `templates/hermes-unit-alert.sh.j2`

- Upstream file: —
- Decision: KEEP
- Reason: brain probe with fleet pause/resume plus an `OnFailure=` pager on a timer; upstream is inactivity-only, systemd has no notifier
- Evidence: `tasks/brain_watchdog.yml`

## `templates/hermes-credential-canary.py.j2`

- Upstream file: —
- Decision: KEEP
- Reason: named-profile credential presence and route check; no upstream counterpart
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

## `templates/hermes-cron-drain-restart.py.j2`

- Upstream file: `cron/jobs.py`
- Decision: KEEP
- Reason: drain-before-restart wrapper for the gateway unit; upstream has cron pause/resume but no drain-then-restart helper
- Evidence: [jobs.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/cron/jobs.py)

## `templates/hermes-pr-reconcile.py.j2`

- Upstream file: —
- Decision: KEEP
- Reason: replay of missed webhook pull_request events; no upstream counterpart
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

## `templates/hermes-repo-crawl.py.j2`

- Upstream file: —
- Decision: KEEP
- Reason: daily repo-crawl pre-check; no upstream counterpart
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

## `templates/kanban-digest.py.j2`

- Upstream file: `gateway/kanban_watchers.py`
- Decision: KEEP
- Reason: deterministic no-LLM board digest; upstream ships no board digest
- Evidence: [kanban_watchers.py](https://github.com/NousResearch/hermes-agent/blob/v0.23.0/gateway/kanban_watchers.py)

## `templates/splunk-digest.py.j2`, `templates/splunk-triage.py.j2`, `templates/splunk-wired-trajectory.py.j2`

- Upstream file: —
- Decision: KEEP
- Reason: Splunk integrations; upstream has no Splunk surface
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

## `templates/vikunja-bridge.py.j2`

- Upstream file: —
- Decision: KEEP
- Reason: Vikunja-to-board bridge daemon; upstream has no Vikunja integration
- Evidence: [v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4)

Patches retired earlier (cron exit-code propagation, compressor-scan clamp,
completion-gate arity, goal-loop quiet flag, protocol-violation handling,
runner-reported failure routing, inactivity-comparison guard) are documented
in the comments of `tasks/pinned_patches.yml` and the patch files they used
to live in; their assertions are retargeted at the upstream text that now
supplies each behavior.
