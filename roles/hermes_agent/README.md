# hermes_agent

Deploys the **[NousResearch Hermes Agent](https://github.com/nousresearch/hermes-agent)**
— the self-improving **autonomous agent** — headless in a dedicated LXC on the AI
VLAN.

> This is **not** the `ollama` / `open_webui` (`hermes-infer` / `hermes-chat`) LLM
> *serving* stack. Those serve the Hermes-4 *model*; this role runs the *agent*,
> which uses that model (or any OpenAI-compatible endpoint) as its brain.

## What it does

- Installs Hermes Agent system-wide via the official installer (bundles Python/uv +
  Node), once, behind a `creates` guard. The installer is fetched from the pinned
  release tag's raw URL and **sha256-verified before it runs** — never
  `curl <url> | bash` of a moving remote script — and `--branch <tag>` pins the
  app checkout to the same release. The Hermes daemon owns subsequent updates
  (`hermes update`) — Ansible owns only the platform, so converge stays idempotent.
- Runs the `hermes gateway` daemon under a dedicated non-root `hermes` user via
  systemd. The gateway drives the built-in **cron** scheduler and the **Kanban**
  dispatcher (autonomy) even with no messaging platform configured.
- `HERMES_HOME` (`/var/lib/hermes/.hermes`) lives on a dedicated ZFS data volume —
  memory, skills, profiles, the Kanban DB, sessions and logs — so it is snapshotted
  and replicated to the DR node (the agent's accumulated knowledge is irreplaceable).
- Points the model backend at the LiteLLM router (`Qwen3-Coder-30B-A3B` via
  `llm.<subdomain>/v1`, OpenAI-compatible, 262144 context); sets memory provider to **Hindsight** (best self-hostable
  June 2026) alongside the always-on `MEMORY.md`/`USER.md`; caps `agent.max_turns`
  so a runaway loop can't pin the GPU overnight.
- Wires the Slack gateway (Socket Mode) via five env vars in `.env`, read
  directly by Hermes' own Slack adapter — no `config.yaml` changes needed.
  All five default to empty, so the gateway simply runs Slack-free until they
  are set.
- Seeds a daily cron job that summarizes the homelab AI fabric status and posts
  it to the Slack home channel; activation happens on the next converge.

## Installation

This role ships as part of this repository (`ansible-proxmox-ai`) — no
separate installation. The role itself fetches and sha256-verifies the pinned
Hermes installer on the target, so the LXC only needs base connectivity and
apt.

## Usage

Run the role against its inventory group:

```bash
doppler run -- uv run ansible-playbook \
  -i inventory/hosts.yml playbooks/site.yml \
  --tags hermes_agent
```

## Key variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `hermes_agent_home` | `/var/lib/hermes` | data-volume mount = the user home |
| `hermes_agent_model_base_url` | `https://llm.{{ PROXMOX_SUBDOMAIN }}/v1` (router) | the brain endpoint |
| `hermes_agent_model` | `Qwen3-Coder-30B-A3B` | model id (LiteLLM router alias) |
| `hermes_agent_memory_provider` | `hindsight` | external memory provider |
| `hermes_agent_max_turns` | `90` | agentic-loop budget |
| `hermes_agent_slack_bot_token` | `""` | Slack bot OAuth token (`xoxb-…`) |
| `hermes_agent_slack_app_token` | `""` | Slack app-level token for Socket Mode (`xapp-…`) |
| `hermes_agent_slack_allowed_users` | `""` | comma-sep Slack member IDs allowed to DM the bot |
| `hermes_agent_slack_home_channel` | `""` | Slack channel ID for proactive posts |
| `hermes_agent_slack_home_channel_name` | `""` | Slack channel display name |

## Group / invocation

Targets `hermes_agent_group`, derived from the `hermes-agent` tag in `load_tofu.yml`.
Run via `site.yml` (`--tags hermes_agent`).

## Not yet live-validated

Verify on the first converge: (a) `install.sh` runs clean non-interactively as root
on a minimal Debian LXC; (b) `hermes gateway run --replace` stays up headless with no
messaging platform; (c) Hindsight initialises from `config.yaml` alone (it may need
its client package on first run — `memory status` check is non-fatal so it surfaces
without failing the converge). Single-profile first; profiles + Kanban teams + a
messaging gateway are a documented follow-up (the whole `HERMES_HOME` is already
persisted for them).

## LLM knowledge base (llm-wiki)

Enables the bundled `research/llm-wiki` skill so Hermes builds and maintains an
interlinked Markdown "second brain" from raw sources (build / query / lint /
maintain, with SHA256 source-drift detection). The wiki lives at `WIKI_PATH` =
`{{ hermes_agent_wiki_path }}` (`/var/lib/hermes/wiki`) — under the persistent
ZFS volume, so it is snapshotted and replicated. A nightly cron seeds a
lint/health-check. Context compression is enabled (`summary_model` pointed at the
router, since the upstream Google default is unreachable here) so long autonomous
sessions don't overflow.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_wiki_enabled` | `true` | enable llm-wiki + create the wiki dir |
| `hermes_agent_wiki_path` | `{{ hermes_agent_home }}/wiki` | persistent wiki root (`WIKI_PATH`) |
| `hermes_agent_context_compression_enabled` | `true` | auto-shrink long sessions |
| `hermes_agent_context_compression_threshold` | `0.85` | compress at 85% of context |
| `hermes_agent_nightly_wiki_cron_*` | — | nightly lint/health-check cron |

## Autonomous GitHub docs-contributor

Gives Hermes a **read public dryvist repos + open signed, draft, no-merge doc PRs**
capability against `dryvist/docs` and `dryvist/docs-starlight`, via a dedicated
GitHub App (`hermes-docs-bot`). Commits are authored through the
`createCommitOnBranch` GraphQL mutation so GitHub marks them **Verified/signed**
(a plain `git push` is rejected by the org's required-signatures ruleset). The
bundled `dryvist/docs-pr` skill enforces the guardrails: draft-only, attribution
triad, dated branches, `docs:` Conventional-Commit titles, per-repo/day caps +
de-dup, secret redaction, and absolute privacy routing (sensitive → docs-starlight
only). **No-merge** is guaranteed by the org ruleset (human review + signatures,
the App is not a bypass actor), not by the token scope.

App creds are delivered from OpenBao `secret/ai/hermes` (`bao_local_llm_secrets`)
with an env fallback; the PEM is written to `{{ hermes_agent_hermes_home }}/github-app.pem`
(`0600`, `no_log`). The role stays inert until the creds are set.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_github_app_id` | `""` | GitHub App ID (bao/env) |
| `hermes_agent_github_app_installation_id` | `""` | App installation ID (bao/env) |
| `hermes_agent_github_app_private_key` | `""` | App PEM (bao/env; written to a 0600 file) |

Helper unit tests live with the skill in
[nix-hermes](https://github.com/dryvist/nix-hermes)
(`data/skills/dryvist/docs-pr/tests/`) — run `python -m pytest` from that
skill dir (all guardrail logic, no network).

## Content bundle (nix-hermes)

The dryvist skills (docs-pr, github-issues, zammad-incidents, splunk-monitor)
and `SOUL.md` are CONTENT owned by the
[nix-hermes](https://github.com/dryvist/nix-hermes) flake, pinned here by
`hermes_agent_bundle_flake_ref` (a release tag). The converge builds that ref
on the **controller** (`nix build`, guarded by a Layer-1 assert) and
byte-copies the result into `$HERMES_HOME` — the guest never needs nix.
`SOUL.md` is composed at build time from `ai-assistant-instructions`'
`autonomous-base.md` plus the Hermes variant, so no vendored copy can drift.
Renovate bumps the pin on each nix-hermes release; edit skills/persona there,
never in this role.

## GitHub issues & projects

Delivers a fine-grained PAT (`GH_PAT_WRITE_PROJECT_ISSUES`) into `.env` giving
Hermes **read/write Issues across all repos** and **read/write Projects (v2) in
the `dryvist` org** — for triaging, creating and updating issues and managing
project boards. It is deliberately least-privilege: **not** for code commits (that
is the signed `docs-pr` / GitHub App path) and **not** for merges. Bao-first
(`secret/ai/hermes`, `bao_local_llm_secrets`) with an env fallback; empty until the
token is set. The bundled `dryvist/github-issues` skill documents the REST (issues)
and GraphQL (Projects v2) calls and the usage guardrails.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_github_issues_pat` | `""` | issues + org-projects PAT (bao/env) |

## Operational log shipping (index=hermes)

Ships the `hermes-*` systemd units' journal logs (gateway, brain watchdog, brain
sync) to a dedicated Splunk `index=hermes`, so agent health is searchable apart
from the shared `os` index. A drop-in rsyslog ruleset forwards only lines whose
`programname` starts with `hermes` over TCP to the `hermes_agent` AI ingest
listener (`syslog.${PROXMOX_SUBDOMAIN}`, port from `tofu_data`), then `stop`s
them so they never also double-ship into `os`. Mirrors the `openbao_audit`
shipping pattern. The port/index/sourcetype are the single tofu-constants
source of truth (`ai_log_routing.hermes_agent`); Cribl Stream's syslog input and
the `hermes` index are provisioned by `ansible-proxmox-apps` / `ansible-splunk`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_syslog_route_enabled` | `true` | Deploy the rsyslog forward |
| `hermes_agent_syslog_host` | `syslog.{{ PROXMOX_SUBDOMAIN }}` | ingest FQDN |
| `hermes_agent_syslog_port` | `ai_log_routing.hermes_agent.port` (tofu) | ingest TCP port |

## Splunk search access

Registers the **Splunk MCP Server** (Splunkbase 7931, deployed by `ansible-splunk`)
as an HTTP MCP server in `~/.hermes/config.yaml` (`mcp_servers.splunk`), so Hermes
can query the environment — `run_splunk_query`, `get_indexes`, `get_sourcetypes` —
with its own scoped identity. The URL and Bearer token are referenced as
`${SPLUNK_MCP_URL}` / `${SPLUNK_MCP_TOKEN}` and resolved from `.env` at connect
time, so neither the endpoint nor the token ever lands in `config.yaml`.

Creds come from the shared OpenBao `secret/ai/mcp/splunk` path (merged into
`bao_local_llm_secrets`) with an env fallback. `ansible-splunk` publishes the
existing shared Splunk service identity as `SPLUNK_MCP_URL` and
`SPLUNK_MCP_TOKEN`. The
`mcp_servers.splunk` entry is omitted until the URL is set, so the agent starts
cleanly before the creds exist.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_splunk_mcp_enabled` | `true` | Register the Splunk MCP server |
| `hermes_agent_splunk_mcp_url` | `""` | Splunk MCP Server endpoint (bao/env) |
| `hermes_agent_splunk_mcp_token` | `""` | Bearer token (bao/env) |

## Splunk monitoring (self-directed 24/7 analyst)

On top of raw search access, the role turns Hermes into a **self-directed SIEM
analyst**. It deploys the `dryvist/splunk-monitor` skill and seeds a small fleet
of cron jobs that carry it. The skill encodes two things that sit together:

- **Hard query-safety rails** — every search must be bounded (`tstats` / `stats` /
  `head N ≤ 100`, an explicit narrow time window, project only needed fields). This
  is what stops an unbounded search from flooding the agent's context and crashing
  the run. The rails are non-negotiable.
- **Free direction** — *what* to look for is Hermes' call. The skill teaches an
  investigative method (recall known baselines → orient → hunt → confirm → record →
  decide delivery) and offers lenses, not a checklist. Hermes learns the
  environment over time and invents its own angles.

Each cron job runs in a **fresh, isolated agent session**, so context never builds
up run to run. Anomaly jobs stay silent when nothing is wrong: a run that ends in
the `[SILENT]` marker suppresses delivery entirely, so a normal sweep costs zero
notifications. Findings are written to memory (baselines + open issues, for
dedup), and durable knowledge is captured as `llm-wiki` pages (RAG).

**Routing (3-tier, 2026-07-18):** Slack output is split by audience, not by
job. The **firehose channel** (`SLACK_FIREHOSE_CHANNEL` →
`hermes_agent_firehose_deliver`) receives every verbose routine report —
`splunk-digest`, `github-triage`, `homelab-ai-fabric-status` (now 24/7), and
the `zammad-review` working report — posted every run, in full. The **home
channel** is the curated operator surface: the once-daily `daily-summary`
rollup (delta-only, no tables, ≤15 lines) and nothing routine. **DMs stay
urgent-only**: anomaly alerts (`slack:<member-id>`, silent-unless-anomaly) and
newly appeared Zammad incidents. The quiet deep-dive research run still saves
locally only (`--deliver local`). With no firehose channel configured, firehose
jobs fall back to the home channel (the original single-channel behavior).

**Zammad review (`zammad-review`, every 2h):** proactively reads open
incidents across ALL queues, proves finished ones complete (resolving them
with evidence — not recommending), enriches open ones with genuinely new
findings, and DMs the operator about incidents that appeared since its last
run. Gated on the Zammad URL + token alongside the Slack gates.

**Delta discipline (canonical surface, not double-reported).** The digest is
the canonical surface for ongoing/known findings; `splunk-triage`'s DM recalls
the digest's last-posted state from memory before alerting and stays silent
when its top finding is already covered there — the DM is for genuinely NEW
or ESCALATING findings only. The digest itself fingerprints its own findings
against its last post: unchanged → a one-line "still open" update; changed, or
once per day (the anchor hour), → the full digest. `github-triage` applies the
same fingerprint-and-collapse pattern to its top-5 list. All three reuse the
existing memory tool for this — no new state infrastructure.

**Fresh posts, not one thread.** Each cron run is an isolated session, so its Slack
output is delivered **flat/top-level** (a new message each time) rather than threaded
under a single ever-growing root. This is set in `config.yaml`'s `platforms.slack`
block via `reply_in_thread: false` + `cron_continuable_surface: in_channel`
(`hermes_agent_slack_reply_in_thread` / `hermes_agent_slack_cron_continuable_surface`),
rendered only when Slack is configured.

| Card | Slot cadence | Posture |
| --- | --- | --- |
| `splunk-triage` | hourly | broad anomaly hunt |
| `splunk-security` | every 6h | security lens |
| `splunk-parsing` | daily | data-quality / parsing lens |
| `splunk-deepdive` | daily | characterize one index → wiki + memory |
| `splunk-digest` | hourly | "what I'm seeing + current normal" heartbeat |

Each workload is gated on Hermes being able to **both** query Splunk
(`hermes_agent_splunk_mcp_url` set) **and** deliver to Slack (bot + app tokens +
home channel set) — a card whose enqueuer is not enabled is never created. When
Hermes finds a signal worth watching continuously it may file its own follow-up
card and surface it in the next digest.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_splunk_monitor_enabled` | `true` | Deploy the skill + enable the Splunk cards |
| `hermes_agent_splunk_*_cron_name` / `_schedule` / `_prompt` | — | per-workload overrides |

## Recurring work runs on the Kanban board (not agentic cron)

Every recurring workload — the Splunk fleet above, `github-triage`,
`daily-summary`, `zammad-review`, `homelab-ai-fabric-status`, the nightly wiki
pass — is a **Kanban card**, not an agentic cron job. The gateway's in-process
board dispatcher (`config.yaml` `kanban:` block) spawns a **fresh worker session
per card**, so a corrupted session can never carry forward between runs
(INC-17120). Kanban has no native recurrence, so a thin fleet of `cron create
--no-agent --script` jobs — one per workload, on the same schedule the old
agentic cron used, plus one daily safety net — enqueues the cards. These crons
run a script only (no LLM session, nothing to poison); `hermes cron list` is
now script-only. On a guest provisioned before the migration, the converge
removes the old bare-named agentic crons
(`hermes_agent_superseded_agentic_cron_names`) so they cannot double-fire
alongside their `-enqueue` twins. Each card carries an idempotency key
`<job>-<slot>`, so a
double-fire or backfill never duplicates a card.

A self-perpetuating **8h reviewer** card (00:00 / 08:00 / 16:00 UTC) reviews the
last 8h of board activity, files follow-ups for anything missed or broken, posts
a digest to `#hermes-all`, and creates the next slot's reviewer card as `blocked`
so it cannot run early; the next enqueuer fire unblocks it. The daily safety-net
enqueuer (`all --backfill`) re-creates any missing card and unblocks any due
blocked card, so the reviewer chain self-heals if a link is ever dropped.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_kanban_cards` | — | the per-workload card table (title, cadence, schedule, prompt var, skills) |
| `hermes_agent_slack_hermes_all_channel` | firehose channel id | channel each card's completion summary posts to |
| `hermes_agent_kanban_reviewer_schedule` | `0 */8 * * *` | the 8h reviewer slots |
| `hermes_agent_kanban_safety_net_schedule` | `33 4 * * *` | daily chain-break backfill sweep |

## Inbound job-submission API (sanctioned non-exec path)

The upstream `api_server` gateway platform, enabled when
`hermes_agent_api_server_key` is present (bao-first, `secret/ai/hermes`
`HERMES_API_SERVER_KEY`). It is the **sanctioned way to submit work to the
agent without touching the guest** — no `pct exec`, no SSH-in-and-run:

- `POST /v1/runs` — enqueue an agent run (`{"input": "<prompt>"}`),
  returns `202` + `run_id`; poll `GET /v1/runs/{run_id}` (or stream
  `/v1/runs/{run_id}/events`).
- `/api/jobs` — full cron-job CRUD (create/pause/resume/run), the REST
  equivalent of `hermes cron …`.
- `GET /health` — unauthenticated liveness (everything else requires
  `Authorization: Bearer <key>`; upstream refuses to start the platform
  keyless).

Traefik fronts it as `https://hermes-api.<subdomain>` (tofu ingress row;
port DRY from `service_ports.hermes_api`); the guest firewall scopes the
port to internal sources. Distinct from the webhook receiver below: webhooks
are pre-declared event triggers, this is arbitrary job submission. The
post-converge gate probes `/health` and asserts a keyless `POST /v1/runs`
is refused with 401.

Concurrency is capped (`hermes_agent_api_max_concurrent_runs`, rendered as
`gateway.api_server.max_concurrent_runs`): the brain is one shared serving
deployment the cron fleet already uses, so over-cap submissions get
`429 + Retry-After` at the door instead of stacking prefills on the GPU.
Upstream already provides per-run `cancelled` state and `POST
/v1/runs/{run_id}/stop`; idempotency keys and a priority queue on `/v1/runs`
are upstream feature gaps tracked as a build-out issue, not role config.

## Curriculum (graded five-job eval, versioned)

`files/curriculum/` owns the manifest, grading, and submission workflow for
the repeatable job set that measures whether the agent is actually useful.
Prompt bodies come from the pinned `ai-llm-prompts` catalog. The complete
curriculum is deployed to `$HERMES_HOME/curriculum/` on every converge.

| Artifact | Role |
| --- | --- |
| `curriculum.yml` | Canonical manifest: order, budgets, expected skills, and each job's **machine-checkable `success_checks`** |
| `jobs/*.md` | Five catalog-backed prompts, materialized during converge and submitted verbatim as `POST /v1/runs` input |
| `grading-sheet.md` | Four 0-3 dimensions per job + verified-claim spot checks + the cross-job omissions check |
| `escalation-rubric-schema.md` | The feature schema (F1-F8) the graded runs populate to fit deep-vs-broad tier routing |
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
`success_checks` list per job. Job ids follow clustered/normal naming (the original
`night-orient` draft id shipped here as `orient`).

## Runner-enforced tool policy (per job class)

A submitted `input` — and everything a job retrieves while running — is
untrusted text that can carry prompt injection. The **runner's toolset
resolution**, not the prompt, decides what each job class may load; injected
instructions cannot widen a toolset list the runner never registered. Policy
is plain data in `defaults/main.yml`:

| Layer | Rendered as | Scope |
| --- | --- | --- |
| `hermes_agent_disabled_toolsets` | `agent.disabled_toolsets` | Global deny floor; no allowlist can widen past it |
| `hermes_agent_api_server_toolsets` | `platform_toolsets.api_server` | API-submitted runs (untrusted input) |
| `hermes_agent_cron_toolsets` | `platform_toolsets.cron` | The scheduled fleet (upstream also hard-blocks cronjob/messaging/clarify in cron) |

The allowlists deliberately exclude `cronjob` (no injected persistence),
`browser`, `delegation`, and `clarify`; Layer-1 asserts fail the converge if
any of those creep back in or a denied toolset is simultaneously allowlisted.
Enabled MCP servers (splunk/context7/codex) layer onto the allowlists by
upstream's platform-tools semantics. The interactive Slack surface keeps the
upstream default (operator-driven, allowed-users gate) minus the deny floor.

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
3. **On a transition** — `hermes cron pause` (or `resume`) the role-seeded fleet
   (`hermes_agent_seeded_cron_names`; user/agent jobs are never touched) and alert
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

### Watchdog self-monitoring ("who watches the watchdog")

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

## Brain runtime source (OpenBao — no rebuild to re-point)

tofu/Ansible seed the **starting** brain (`ai_default_model`, baked into
`config.yaml` at converge) but do not **own** it. A second, independent
`systemd` timer — `hermes-brain-sync.timer`, every
`hermes_agent_brain_sync_interval` (default 5 min), run as **root**, separate
from the `hermes` user's own gateway process and `.env` — polls the
`ai-public` OpenBao domain's non-secret `secret/ai/public/brain` (field
`active_model`; see `roles/openbao_secrets`) and, only when the value both
**changed** and **validates live** against the router's own
`GET /v1/model/info` (a tuned entry with a real `max_input_tokens` — never
the `"*"` wildcard passthrough; the live counterpart to the converge-time
compress-death-trap guard in `roles/llm_router/tasks/assert.yml`), rewrites
`config.yaml`'s `model.default` and restarts `hermes-gateway`.

Re-pointing the fabric brain day-to-day is now an OpenBao write, not a
converge:

```sh
bao kv patch secret/ai/public/brain active_model=<new-model-id>
```

The change reaches Hermes within one poll interval, with no tofu/ansible run.
A poll failure (OpenBao unreachable, router unreachable, candidate not a
tuned router entry) always just **keeps the currently-running brain** — it
never blanks it — and `ai_default_model` (itself bao-first since 2026-07-18,
same `ai-public` domain) is both the converge-time seed and the fallback a
sync cycle falls back to. The brain-health watchdog above reads the same
live state file (`hermes_agent_brain_sync_state_file`), so it always probes
whichever model is actually configured right now, not a stale converge-time
value.

The `ai-public` domain deliberately uses its **own**, narrowly read-only
AppRole — never `local-llm`'s broader `ai/*` credential — because its
role_id/secret_id are the ones copied onto this guest (a dedicated
`0600` env file, kept out of Hermes' own `.env` so the credential never
enters the agent's own environment). See `roles/openbao_secrets/README.md`
for the rationale and the companion `ansible-proxmox-apps` AppRole/policy
this domain still needs before it resolves live.

**Deliberately NOT extended** to `hermes_agent_compression_model` (pinned to
a literal so a brain repoint can never silently drag the ≥64K-context
compression path with it) or to `hermes_agent_memory_llm_model` (only
rendered under the `local_embedded` Hindsight mode, not the live
`local_external` default).

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_brain_sync_enabled` | `true` | Deploy + start the brain-sync timer |
| `hermes_agent_brain_sync_interval` | `5min` | Poll cadence (`OnUnitActiveSec`) |
| `hermes_agent_brain_sync_bao_path` | `ai/public/brain` | KV v2 data path (mount `secret`) |
| `hermes_agent_brain_sync_bao_field` | `active_model` | Field holding the candidate model id |
| `hermes_agent_brain_sync_state_file` | `/etc/hermes-brain-sync/current-model` | Live pointer, world-readable, shared with the watchdog |

## Live docs (Context7)

Registers Context7's hosted HTTP MCP server (`mcp_servers.context7`) so Hermes
can pull **current, version-specific library/framework docs** on demand instead
of relying on stale training data. The API key is referenced as
`${CONTEXT7_API_KEY}` (resolved from `.env`), bao-first (`secret/ai/hermes`) with
env fallback; the entry is omitted until the key is set.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_context7_mcp_enabled` | `true` | Register the Context7 MCP server |
| `hermes_agent_context7_api_key` | `""` | Context7 API key (bao/env) |

## Escalation (Codex via MCP)

Registers `codex mcp-server` (OpenAI's Codex CLI) as an MCP tool
(`mcp_servers.codex`) — a deliberate escalation path for problems worth a
stronger model, or a session that's stuck/looping. This is **not** automatic
on-error fallback (Hermes' own `fallback_providers` feature is intentionally
unused here); tool use is inherently a per-call, model-chosen decision, so
the agent reaches for Codex only when it judges the problem warrants it, the
same way it decides whether to call any other tool.

Codex runs under a completely separate, low-privilege OS user —
`codex-runner`, provisioned by the sibling `codex_runner` role on the same
host — never as `hermes`. The MCP entry invokes it through a single-command
`sudo` grant (`hermes` → `codex-runner`, exactly `codex mcp-server`, nothing
else); Hermes never gains filesystem access to that user's ChatGPT-OAuth
credential, so the token itself is not directly readable by the agent even
though the agent can fully use the tool.

Codex's OAuth login is a manual, one-time, interactive step that cannot be
automated by Ansible — see `roles/codex_runner/README.md` for both bootstrap
options (fresh `codex login`, or copying an already-authenticated
`~/.codex/auth.json`). Until that's done, the MCP entry is present but every
call to it errors; the daemon itself starts and runs normally regardless.

OpenRouter is reachable with **no Hermes-side wiring**: the `llm_router` role
registers OpenRouter models under their real upstream ids (first:
`nvidia/nemotron-3-ultra-550b-a55b:free`), with one OpenBao-held key **per
model** under `secret/ai/saas/openrouter` — Hermes just names the real model
id like any other. The old account-wide `OPENROUTER_API_KEY` parked in
`secret/ai/hermes` is superseded by those per-model keys and should be retired
once they are seeded.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_codex_mcp_enabled` | `true` | Register the Codex MCP server |
