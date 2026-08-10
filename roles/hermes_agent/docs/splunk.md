# Operational log shipping (index=hermes)

Ships every `hermes-*` systemd unit record and active `.hermes/logs/*.log` file
to a dedicated Splunk `index=hermes`, so agent health is searchable apart from
the shared `os` index. The rsyslog unit match also captures child processes
whose `programname` is not `hermes`. Both sources forward to the `hermes_agent` AI ingest
listener (`syslog.${PROXMOX_SUBDOMAIN}`, port from `tofu_data`), then `stop`s
them so they never also double-ship into `os`. Mirrors the `openbao_audit`
shipping pattern. The port/index/sourcetype are the single tofu-constants
source of truth (`ai_log_routing.hermes_agent`); Cribl Stream's syslog input and
the `hermes` index are provisioned by `ansible-proxmox-apps` / `ansible-splunk`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_syslog_route_enabled` | `true` | Deploy the rsyslog forward |
| `hermes_agent_log_directory` | `.hermes/logs` | Active Hermes file logs to forward |
| `hermes_agent_syslog_host` | `syslog.{{ PROXMOX_SUBDOMAIN }}` | ingest FQDN |
| `hermes_agent_syslog_port` | `ai_log_routing.hermes_agent.port` (tofu) | ingest TCP port |

Use this public-safe timeline search to see each event at second precision and
the extracted model latency where Hermes emitted it:

```spl
index=hermes
| eval log_stream=if(match(_raw, "hermes-file"), "file", "systemd")
| eval duration_seconds=round(latency, 3)
| sort 0 _time
| table _time log_stream session model duration_seconds _raw
```

## Splunk search access

Registers the **Splunk MCP Server** (Splunkbase 7931, deployed by `ansible-splunk`)
as an HTTP MCP server in `~/.hermes/config.yaml` (`mcp_servers.splunk`), so Hermes
can query the environment — `run_splunk_query`, `get_indexes`, `get_sourcetypes` —
with its own scoped identity. The URL and Bearer token are referenced as
`${SPLUNK_MCP_URL}` / `${SPLUNK_MCP_TOKEN}` and resolved from `.env` at connect
time, so neither the endpoint nor the token ever lands in `config.yaml`.

The URL is the shared agentgateway `/splunk` route (built from
`PROXMOX_SUBDOMAIN` in the role defaults, non-secret), same posture as the
context7/docs routes — the gateway federates to the Splunk MCP Server. The
Bearer token stays bao-first: it comes from the shared OpenBao
`secret/ai/mcp/splunk` path (merged into `bao_local_llm_secrets`) with an env
fallback, and is the remaining credential — empty until seeded. The
`mcp_servers.splunk` entry is omitted only if the URL is empty, so the agent
starts cleanly regardless.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_splunk_mcp_enabled` | `true` | Register the Splunk MCP server |
| `hermes_agent_splunk_mcp_url` | `mcp.<sub>/splunk` | Agentgateway route (non-secret) |
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

**Routing (3-tier, 2026-07-18; see `docs/HERMES_OPS.md` for the newer 4-channel
scheme, 2026-07-31):** Slack output is split by audience, not by
job. The **firehose channel** (`SLACK_FIREHOSE_CHANNEL` →
`hermes_agent_firehose_deliver`) receives every verbose routine report —
`github-triage`, `homelab-ai-fabric-status` (now 24/7), and the
`zammad-review` working report, posted every run in full, plus the
script-fed `splunk-status-digest` cron (posts on anything critical or novel,
and otherwise at least once every `HEARTBEAT_HOURS` — see "Delta discipline"
below; the LLM `splunk-digest` card this described is removed, 2026-08-01).
The **home
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

**Delta discipline (own state, not double-reported).** `splunk-triage`'s DM
recalls its OWN last-posted findings from memory (key `splunk-triage-last`)
before alerting and stays silent when its top finding is already covered
there — the DM is for genuinely NEW or ESCALATING findings only. (Until
2026-08-01 it recalled the LLM `splunk-digest` card's key instead; once that
card was removed the recall was a dangling read that always found nothing —
fixed in the `ai-llm-prompts` catalog, guarded at converge time.) The
script-fed status digest posts the real
per-index volumes plus their delta against the previous run whenever anything
is CRITICAL or genuinely novel anywhere in its escalation ladder (index, host,
then sourcetype/composition), exactly as before. **Heartbeat gate (operator
decision, 2026-07-26):** a run with nothing critical and nothing novel now
goes `[SILENT]` unless `HEARTBEAT_HOURS` (a module constant in
`splunk-digest.py.j2`, currently 6) has elapsed since the last real post — the
prior rule posted a "Health state unchanged" boilerplate line on every single
quiet hour (38 of 40 runs carried zero information in one UTC day). A CRITICAL
finding is exempt and always posts, every run, for as long as it holds, so an
ingest anomaly is never delayed or hidden by this gate; the fingerprint still
labels whether the health picture moved, the heartbeat clock only decides
whether a *quiet* state gets restated.

**Waking hours (2026-07-26).** The status digest runs `52 7-23 * * *` — 17
runs/day, not 24. Overnight posts were read the next morning anyway, so the
job simply does not run between 00:00 and 06:59. The two mechanisms are
independent: the *schedule* decides whether a run happens, `HEARTBEAT_HOURS`
decides whether a quiet run says anything. The first run after the gap
(07:52) is always ≥ `HEARTBEAT_HOURS` past the last post, so the morning
always opens with a real state report. A CRITICAL condition starting after
23:52 is not surfaced by this job until 07:52 — accepted deliberately: the
digest is a status surface, and urgent alerting is `splunk-triage`'s
silent-unless-anomaly DM path, which keeps its own unchanged schedule.

`github-triage` does apply the
fingerprint-and-collapse pattern to its top-5 list, reusing the existing
memory tool — no new state infrastructure.

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

The `splunk-digest` card that used to sit here (hourly "what I'm seeing +
current normal" heartbeat) is REMOVED (2026-08-01) — see `docs/HERMES_OPS.md`
"Kanban cards" for why. Its topic is covered by the script-fed
`splunk-status-digest` cron instead (no LLM in its fact path); see "Script
crons" in `docs/HERMES_OPS.md`.

Each workload is gated on Hermes being able to **both** query Splunk
(`hermes_agent_splunk_mcp_url` set) **and** deliver to Slack (bot + app tokens +
home channel set) — a card whose enqueuer is not enabled is never created. When
Hermes finds a signal worth watching continuously it may file its own follow-up
card and surface it in the next digest.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_splunk_monitor_enabled` | `true` | Deploy the skill + enable the Splunk cards |
| `hermes_agent_splunk_*_cron_name` / `_schedule` / `_prompt` | — | per-workload overrides |
