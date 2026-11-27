# Operating profiles

Choosing a profile is the **default action** for new recurring work — not an
advanced option. `default` is not the absence of a choice; it is correct only
for cross-domain, board-meta, or write-bearing GitHub work (say why in the PR).

A **profile** is a fully independent `HERMES_HOME` directory (own
`config.yaml`, `.env`, `SOUL.md`, skills) that a Kanban card selects
via its `assignee` field. The dispatcher spawns
`hermes -p <assignee> chat ...` with `HERMES_HOME` pointed at that profile's
directory, so the assignee is the **entire tool/credential envelope** the
worker runs under — MCP servers, native toolset floor, `.env` secrets, and
skills. Named profiles live at `{{ hermes_agent_hermes_home }}/profiles/<name>/`;
the `default` profile is `{{ hermes_agent_hermes_home }}` itself.

There is **one shared gateway, dispatcher, Kanban board, and Slack bot** for
every profile — no per-profile gateway service exists. One Slack socket per
identity: a profile's `.env` renders `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN` blank
(`templates/hermes-env-profile.j2`), so the main gateway's adapter is the only
Socket Mode connection and the runtime never sees one app token twice. Cron is
the one exception (native-cron reframe): a direct-cron job on a named profile
(`hermes_home:` in `hermes_agent_direct_cron_jobs`) gets its own cron store,
which the persistent gateway ticks in that profile's scope and delivers through
the primary's profile route. See `hermes_agent_profiles` in
`defaults/main.yml` for the full design comment, the decision rule, and the
isolation caveat (profile scoping is a config/tool-availability boundary, not
an OS sandbox: every profile runs as the same `hermes` user in the same LXC).

**A profile's `config.yaml` overrides the shared config, it does not merge
with it.** A named profile that omits a section the default profile has (an
MCP server entry, a config block) simply does not get it — there is no
fallback to the shared value. `templates/config-profile.yaml.j2` generates
each profile's `mcp_servers:` block from that profile's own `mcp:` list in
`hermes_agent_profiles`, so a job needing a tool must have that tool named on
its assignee's profile entry, or the worker runs with the tool silently
absent rather than inherited.

**Memory is shared only by memory-enabled profiles.** Ordinary profiles point
at the same Hindsight bank for their agent: the static `bank_id` derives from
`hermes_agent_id`, and no profile sets
`bank_id_template`, so `_resolve_bank_id_template` falls back to that one
agent bank. Different Hermes agents receive distinct banks. This is
deliberate — it is what lets `daily-summary` on the `default` profile recall a
card's findings after its `assignee` moves. The one exception is a profile
entry that sets `memory_bank_id`. `splunk-admin` does, so every Splunk job
shares one Splunk bank (`hermes_agent_splunk_memory_bank_id`). `homelab-admin`,
`github-maint` and `orchestrator` each get their own `<agent>-<profile>` bank,
so they do not read the agent bank. That shared-continuity path no longer
covers these three profiles. A profile with
`memory_enabled: false` (`eval`) and locked profiles get neither a bank nor a
Hindsight config. On the agent that owns the `splunk` system that bank is the
agent bank itself. Within a
bank, a worker can `memory recall` anything another profile of that bank ever
wrote. Nothing written to a shared bank should be treated as private to one
profile.

| Profile | Mission | Has | Must NOT have |
| --- | --- | --- | --- |
| `default` | Cross-domain, board-meta, write-bearing GitHub | broad tool scope; no ordinary-call Kanban | — |
| `splunk-admin` | Read-only SIEM: SPL, alert + report | Splunk + Docs MCP, `splunk-monitor` skill | GitHub, Zammad, other MCP/skills |
| `homelab-admin` | Incidents + fabric health | Docs MCP, `zammad-incidents` skill | Splunk, Vikunja, GitHub, other MCP/skills |
| `github-maint` | Read-only repo review + proposals | Docs MCP, `github-issues` skill, read-only token | GitHub writes, `docs-pr`, Splunk, Zammad, other |
| `orchestrator` | Kanban decomposition and assignment | Vikunja + Docs MCP; follow-up card creation | Operational MCP servers and credentials |
| `tracker-reporter` | Change-only task and incident summaries | Vikunja + router incident MCP; own bank | Kanban, env, skills, other MCP |
| `vikunja-pm` | Task filing, updates, labels, relations and comments | Router Vikunja write tier (no delete); own bank | Kanban, env, skills, other MCP |
| `youtrack-clerk` | Narrow YouTrack requests | Router write tier; local model; own bank | Native toolsets, memory tools, skills, plugins, A2A, other MCP |
| `vikunja-clerk` | Narrow Vikunja task requests | Router clerk tier; local model; own bank | Native toolsets, memory tools, skills, plugins, A2A, other MCP |
| `zammad-clerk` | Narrow Zammad ticket requests | Router write tier; local model; own bank | Native toolsets, memory tools, skills, plugins, A2A, other MCP |
| `semaphore-converge` | Previews and confirmed runs | One router write tier; local-only model | Native toolsets, memory, skills, plugins, A2A, other MCP |
| `eval` | Synthetic-task evaluation | web, skills, todo, context_engine, session_search; local-only key | Memory, cron, Kanban, Slack, GitHub, MCP |

The `tracker-reporter` profile and its two report jobs exist only while
`llm_router_hermes_tracker_reporter_key_enabled` is on. The `vikunja-pm`
profile exists only while `llm_router_hermes_vikunja_pm_key_enabled` is on.
Each clerk profile (`youtrack-clerk`, `vikunja-clerk`, `zammad-clerk`,
`semaphore-converge`) and its router model exist only while its
`llm_router_hermes_<name>_key_enabled` switch is on. Its own `.env` carries its API-server key, and the router serves it
under the profile prefix with a separate bearer. A clerk uses the local review
model, exposes one app-agent MCP server, and has built-in memory disabled. Each
tracker clerk declares its own Hindsight bank with auto-retain and context mode;
context mode adds no memory tools. The tracker clerks have no other memory, and
`semaphore-converge` remains memory-free.

The current inventory enables `youtrack-clerk`, `vikunja-clerk`, and
`zammad-clerk`; `semaphore-converge` remains off.

The clerk's router key is the key that holds its tier: the router turns an
app-agent tier on only when the tier's token is seeded and a roster entry or a
locked profile holds a key for it. A seeded token that nothing holds stays off.

`github-maint` is the one profile whose headline constraint is enforced by a
**credential** rather than by tool availability. It renders the read-only
token under the same `.env` key the `github-issues` skill authenticates with,
so the skill works unchanged and every write call fails at the API. Its
proposals leave through Slack and `kanban_create`; it opens no issues, files
no pull requests, and posts no comments.

**A job selects its profile by `HERMES_HOME` and its tools by the job spec.**
An entry in `hermes_agent_direct_cron_jobs` sets `hermes_home:` to the
profile's directory; creation, lookup, and the profile tick use that store.
The Cron platform baseline excludes Kanban. Only card-filing jobs add it to
their upstream `enabled_toolsets` field. Most use the orchestrator profile;
credential-bound jobs keep their own profile and receive Kanban only for that
job.

**Adding a new profile**: add an entry to `hermes_agent_profiles`
(`mcp`/`env`/`skills`/optional `toolsets` and SOUL addendum source). A locked
clerk declares its name, description, one MCP server, `locked: true`, its
switch and its prompt. A tracker clerk names `<name>.soul.md`, which the role
reads from the prompt pack at the pinned commit and checks against the pinned
manifest sha256; any other locked profile extends `soul-clerk.md.j2`. Shared
defaults supply the rest. Profile addenda
ship in the pinned nix-hermes bundle under `soul/profiles/`; the
orchestrator keeps its role-local addendum. Scope each profile by what it must
**not** reach, not by what it might someday need — a new capability is a new
profile decision made in the PR that adds the work, not a widened existing one.

**Serving a profile over A2A**: a profile entry with an `a2a` mapping is served
on the gateway's A2A listener under `/<profile>`, while
`hermes_agent_a2a_inbound_enabled` is on. Optional keys: `name` (default
`Hermes <profile>`), `description` (default the profile's `description`) and
`advertised_toolsets`.

**Concurrency**: `hermes_agent_kanban_max_in_progress` is the SUM cap across
every profile combined. It takes the shared `ai_agent_default_concurrency`
(`inventory/group_vars/all/`), the same cap the API uses, and that value does
not track any serving profile. Naming a profile never raises real concurrency by
itself; changing the shared cap is a separate, deliberate operator decision.

**Verifying a new profile** (manual, not part of the converge — it burns an
LLM run): see "Profile smoke test" in `docs/HERMES_OPS.md`.

Full page: <https://docs.jacobpevans.com/autonomous-agents/hermes-operating-profiles>
(concept, table, and the decision rule for the public docs site).
