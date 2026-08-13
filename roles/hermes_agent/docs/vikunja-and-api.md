# Vikunja bridge (the operator's board drives Hermes)

The Kanban fleet above is the *recurring* half of the board. The Vikunja bridge
is the *on-demand* half: it lets the operator run Hermes entirely from their own
Vikunja board, without touching Hermes directly.

`hermes-vikunja-bridge.service` is a stdlib-only poll daemon
(`templates/vikunja-bridge.py.j2`) that does three things per tick:

- **intake** — an undone **Ready** task carrying the intake label becomes a card
  via `hermes kanban create --idempotency-key vikunja-<task id>`. The task then
  moves to **In Progress** and gets a comment naming the card.
- **reconcile** — each tracked card is read out of `kanban.db` (READ-ONLY, same
  posture and source as `kanban-digest.py`). Once its run has settled, the
  recorded outcome is written back: a summary comment, a move to **Done** or
  **Blocked**, and `done` on success.
- **ledger** — the task-to-card mapping is a schema-versioned JSON file in the
  Hermes state dir, beside the digests' state.

**Nothing in the relay path is written by a model.** Every comment the bridge
posts is either a literal card id or a summary string copied verbatim out of
`kanban.db`. The LLM does the work *inside* the card; the bridge only reports
what the board recorded — so a wedged or unloaded brain surfaces as a real
`FAILED` comment instead of a plausible-sounding success.

**Single writer.** The card body explicitly forbids the worker from touching
Vikunja itself. One path to the operator's board means two surfaces can never
show contradictory verdicts for the same card.

**It does not use the Vikunja MCP route.** `hermes_agent_vikunja_mcp_enabled`
stays `false`. Every operation the bridge performs — bucket move, comment,
`done` — is a *write*, and the gateway's `/vikunja` route is read-only by
construction (the sidecar holds a READ token). Enabling it would buy the bridge
nothing while adding the documented per-session "failed initial connection"
park, since the route is still not seeded. Flip that flag when the agent should
*read* the board conversationally — an unrelated decision.

## Two endpoint facts, both verified live (2026-07-26)

Both were wrong on the first pass, and **both fail silently** — the daemon
runs, logs nothing, and does nothing. Each is pinned by a check.

1. **The bucket move is `POST`, not `PUT`.** `PUT` returns **405** on the
   running instance, even though upstream's own client docs say `PUT`. The
   route itself is also load-bearing: a task *update* does not move buckets
   (the server only auto-moves on a `done` flip), so sending `bucket_id` in an
   update succeeds and moves nothing.
2. **Tasks are read from `/views/{v}/tasks`, not `/views/{v}/buckets`.** Both
   return the bucket list, but `/buckets` carries only a `count` and **no
   `tasks` key at all**. Reading tasks from it returns an empty list for every
   bucket, forever, with a 200 and no error anywhere — the bridge would poll
   perfectly and never pick up a single task.

One call serves both bucket-id mapping and the ready-task read, which is also
one fewer token permission.

**Concurrency** is *not* managed here. `kanban.max_in_progress` already bounds
how many workers the shared serving deployment carries, and stays the single
source of truth. `hermes_agent_vikunja_bridge_max_intake_per_tick` is only a
rate limit, so one bulk paste into Vikunja cannot create fifty cards at once.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_vikunja_bridge_enabled` | `false` | opt-in — it writes to the operator's real board |
| `hermes_agent_vikunja_bridge_project` | `Hermes` | Vikunja project whose Kanban view is polled |
| `hermes_agent_vikunja_bridge_bucket_ready` | `Ready` | the only **required** bucket — where intake reads from |
| `hermes_agent_vikunja_bridge_bucket_in_progress` / `_done` / `_blocked` | `In Progress` / `Done` / `Blocked` | optional; a missing one skips its move |
| `hermes_agent_vikunja_bridge_intake_label` | `hermes` | second explicit opt-in. Empty means every undone Ready task is fair game |
| `hermes_agent_vikunja_bridge_max_intake_per_tick` | `3` | rate limit, not a concurrency limit |
| `hermes_agent_vikunja_bridge_card_assignee` | `""` | `kanban.default_assignee`, or a name in `hermes_agent_profiles` (asserted) |
| `hermes_agent_vikunja_bridge_token` | `env HERMES_VIKUNJA_API_TOKEN` | **operator-supplied**; see below |

## The credential

`HERMES_VIKUNJA_API_TOKEN` — a **write-scoped** Vikunja API token. Resolved
bao-first via the `local-llm` OpenBao domain (`secret/ai/hermes` — see
`inventory/group_vars/hermes_agent_group.yml`, which overrides the role's
plain-env default above), falling back to the converge environment
(`lookup('env', ...)`, so Doppler or SOPS also satisfy it) when that domain's
AppRole isn't configured. A path-exact `hermes` domain also exists (see
`roles/openbao_secrets/defaults/main.yml`), provisioned ahead of a future
migration off `local-llm`'s broader `ai/*`-style grant — nothing reads
through it yet.

**Already provisioned.** A least-privilege token was minted via the Vikunja API
and stored at `secret/ai/hermes` in OpenBao (key `HERMES_VIKUNJA_API_TOKEN`,
alongside Hermes' other credentials at that same path). Every function above
was then run against the live instance with that exact token.

Its permission set is exactly the operations the bridge performs and nothing
more — no project create, no project delete, no delete of anything:

| Group | Actions | Used for |
| --- | --- | --- |
| `projects` | `read_all`, `views_buckets_tasks` | find the project by title; the bucket move |
| `projects_views` | `read_all` | find the Kanban view |
| `projects_views_tasks` | `read_all` | buckets **with** their tasks |
| `tasks` | `read_one`, `update` | mark done |
| `tasks_comments` | `create` | relay the card's result |

Note `projects_views` and `projects_views_tasks` are **separate permission
groups** from `projects`. The pre-existing `VIKUNJA_MCP_TOKEN_RW` lacks them
and returns 401 listing views, so it cannot drive this bridge — that is why a
new token exists rather than reusing it. Vikunja also requires `expires_at` on
token creation; this one is dated one year out.

Deliberately **not** `ai_runner`'s `VIKUNJA_API_TOKEN`: different guest,
different scope, and one shared credential would hand each the other's
permissions. Enabling the bridge without a token fails the converge rather than
shipping a daemon that can only log `bridge idle`.

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
