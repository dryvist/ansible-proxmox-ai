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
every profile — no per-profile gateway service exists. Cron is the one
exception (native-cron reframe): `hermes cron`'s ticker runs in-process
inside whichever gateway registered a job, and only the default profile's
gateway is persistent, so a direct-cron job on a named profile
(`hermes_home:` in `hermes_agent_direct_cron_jobs`) gets its own cron store
AND its own periodic `hermes cron tick` trigger (`tasks/main.yml`) rather
than sharing the default gateway's. See `hermes_agent_profiles` in
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

**The isolation boundary is tools and credentials — memory is NOT part of
it.** Every profile points at the same Hindsight bank for its agent: the
static `bank_id` derives from `hermes_agent_id`, and no profile sets
`bank_id_template`, so `_resolve_bank_id_template` falls back to that one
agent bank for all of its profiles. Different Hermes agents receive distinct
banks. This is deliberate — it is what lets `daily-summary` on the `default`
profile recall a card's findings after its `assignee` moves — but it means a
`splunk-admin` worker can `memory recall` anything `homelab-admin` (or any
other profile) for the same agent ever wrote. The "Must NOT have" column is a
**tools/MCP/skills boundary only**; it is not a data boundary, and nothing
written to memory by any profile should be treated as private to it.

| Profile | Mission | Has | Must NOT have (tools/MCP/skills — memory is shared across every profile, see above) |
| --- | --- | --- | --- |
| `default` | Cross-domain, board-meta, write-bearing GitHub | everything (unchanged) | — |
| `splunk-admin` | Read-only SIEM: SPL, alert + report | Splunk + Docs MCP, `splunk-monitor` skill | GitHub, Zammad, other MCP/skills |
| `homelab-admin` | Incidents + fabric health | Docs MCP (+ Vikunja/Nautobot later), `zammad-incidents` skill | Splunk, GitHub, other MCP/skills |
| `github-maint` | Read-only repo review + proposals | Docs MCP, `github-issues` skill, read-only token | GitHub writes, `docs-pr`, Splunk, Zammad, other |

`github-maint` is the one profile whose headline constraint is enforced by a
**credential** rather than by tool availability. It renders the read-only
token under the same `.env` key the `github-issues` skill authenticates with,
so the skill works unchanged and every write call fails at the API. Its
proposals leave through Slack and `kanban_create`; it opens no issues, files
no pull requests, and posts no comments.

**A job selects its profile by `HERMES_HOME`, not by a flag.** `hermes cron
create` has no profile-selection flag (unlike `kanban create`'s `--assignee`),
so an entry in `hermes_agent_direct_cron_jobs` sets `hermes_home:` to the
profile's directory instead — the create, the lookup, and the periodic tick
all run against that store. An entry that omits `hermes_home:` runs under the
default profile, which is what the 18 converted jobs did on landing; the
splunk-* jobs, the zammad review, the fabric status, and `github-maint-review`
carry it today.

**Adding a new profile**: add an entry to `hermes_agent_profiles`
(`mcp`/`env`/`skills`/`soul_addendum_file`), add a
`templates/soul-<name>.md.j2` addendum, and scope it by what it must **not**
reach, not by what it might someday need — a new capability is a new profile
decision made in the PR that adds the work, not a widened existing one.

**Concurrency**: `hermes_agent_kanban_max_in_progress` is the SUM cap across
every profile combined (1 today — see the comment on that var). Naming a
profile never raises real concurrency by itself; raising the cap back up is a
separate, deliberate operator decision after the serving tier proves the
capacity.

**Verifying a new profile** (manual, not part of the converge — it burns an
LLM run): see "Profile smoke test" in `docs/HERMES_OPS.md`.

Full page: <https://docs.jacobpevans.com/ai/hermes-operating-profiles>
(concept, table, and the decision rule for the public docs site).
