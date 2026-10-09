# llm_router router_settings seed mode and LiteLLM 1.104.0 schema upgrade

Split out of `roles/llm_router/README.md` ("Editing ladders in the UI") to
keep both files under the repo's per-file token budget
(`.token-limits.yaml`). The router-settings API details below are a historical
source check against LiteLLM 1.102.0; LiteLLM 1.104.0 refactored the proxy
internals, so re-verify those exact paths before relying on them for a later
upgrade. The schema-upgrade section describes the current `litellm==1.104.0` migration
contract.

- **Startup merge direction**: with `store_model_in_db: true`, the database
  row wins over `config.yaml` for every key it carries — `ProxyConfig.
  _update_config_fields`'s `_deep_merge_dicts` (`litellm/proxy/
  proxy_server.py:7205-7281`), called from `_update_config_from_db`
  (`proxy_server.py:7288-`), called from `get_config` at startup
  (`proxy_server.py:5181-5225`). A key the file renders but the database
  never touched keeps the file's value; a key the UI has written wins on
  every subsequent proxy start, restart or not.
- **The UI write path**: Router Settings saves through `POST /config/update`
  (`proxy_server.py:16831`) — merge-per-key, request wins, upserted into
  the `LiteLLM_Config` table. There is no `PUT /fallback/{model}` endpoint in
  this pinned release (verified: the only `/fallback*` route is
  `/fallback/login`, the UI's OAuth redirect, unrelated to fallback config) —
  anything that call shape reports back is not the UI's actual write path.
- **Propagation, no restart needed**: `/config/update` triggers
  `ProxyConfig.add_deployment` (`proxy_server.py:7360`) once immediately, and
  every proxy instance also runs it on its own APScheduler `interval` job,
  every `PROXY_CONFIG_RELOAD_INTERVAL_SECONDS` (`litellm/constants.py:1720`,
  default **30s**). That job's `_add_router_settings_from_db_config`
  (`proxy_server.py:6855`, called from `proxy_server.py:6640`) re-reads the
  row and calls `llm_router.update_settings(**combined_router_settings)`
  live — a fallback or routing-strategy edit made in the UI on one router
  reaches every other router in the pool within ~30 seconds, no restart.
  Models added/edited in the UI (`LiteLLM_ProxyModelTable`) reconcile through
  the same `add_deployment` job and the same interval. Key model-scope
  changes are served from `user_api_key_cache`, in-memory TTL 60s by default
  (`proxy_server.py:1660`, `general_settings.user_api_key_cache_ttl`, read at
  `proxy_server.py:5860`).
  - New in the 1.102.0 source check (additive, not consulted by this role's converge):
    `management_endpoints/router_settings_endpoints.py` adds a Key > Team
    hierarchical `router_settings` lookup ahead of the global one this doc
    describes. The benchmark key uses per-key tag filtering; other seeded keys
    currently rely on global router settings.

## Database (optional)

Set `llm_router_db_host` and the proxy attaches PostgreSQL for the **Adaptive
Router's learned quality estimates**, which LiteLLM loads at startup. Leave it
empty and the router still serves every request — it simply forgets each
restart and reverts to cold-start priors, which is a silent degradation rather
than a visible failure.

Scope is deliberately narrow, and the reasoning is in
`defaults/main/45-database.yml`:

- `store_model_in_db` is **true**, and carries role deployments only. It is
  independent of adaptive routing. A config-file entry stays owned by the
  converge and read-only in the UI, so the catalog keeps its single source of
  truth while roles become editable — see "Roles" in `roles/llm_router/README.md`.
- Spend and error logs are **on**, bounded by a 30-day native retention job,
  and carry no prompts — see `defaults/main/45-database.yml`.
- Credentials are bao-first from `apps/llm-router`, the same field the Postgres
  converge in `ansible-proxmox-apps` uses to create the role, so the two ends
  cannot drift.

## Schema updates: reconcile before startup, then fail closed

`main.yml` runs `prisma db push` as the service user after generating the
client for the installed schema. It does not pass `--accept-data-loss`. On
startup, LiteLLM 1.104.0 uses Prisma migrations and its migration check is
enabled by default; `litellm.env.j2` sets
`ENFORCE_PRISMA_MIGRATION_CHECK=True` explicitly. Do not set
`DISABLE_SCHEMA_UPDATE` or turn off the migration check.

This router's existing database was managed by `db push` and has no
`_prisma_migrations` ledger. LiteLLM 1.104.0's v2 resolver handles that case
only after verifying that the current database schema matches the installed
Prisma model; if the diff is non-empty or setup fails, startup exits. The
resolver's baseline records packaged migrations only after that schema check;
it does not apply old data backfills. This keeps an uncertain schema from
serving silently.

For an existing database upgraded from LiteLLM 1.102.x or earlier, release
notes require two SpendLogs indexes to be created concurrently before the
rollout. The migration package's 1.104.0 entries for these indexes are no-ops,
so the schema push and startup baseline do not create them. Use the exact SQL
and one-member-first rollout below.

### LiteLLM 1.104.0 release and rollout

The role pins `litellm[proxy]==1.104.0`, verified as the current stable release
on 2026-10-05. See the [official release and migration notes](https://docs.litellm.ai/release_notes/v1.104.0/v1-104-0).
The release rejects an unset, empty, or public default master key; this role
checks the configured key without logging its value. Admin UI and `lite` CLI
users sign in again after a completed rolling upgrade.

For an existing database upgraded from 1.102.x or earlier, create both indexes
outside a transaction before the rollout:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS "LiteLLM_SpendLogs_api_key_startTime_idx"
  ON "LiteLLM_SpendLogs"("api_key", "startTime");
CREATE INDEX CONCURRENTLY IF NOT EXISTS "LiteLLM_SpendLogs_litellm_call_id_idx"
  ON "LiteLLM_SpendLogs"("litellm_call_id");
```

Back up the database and confirm the index preflight first. Then rebuild one
pool member through the existing `router-rebuild` template. Check startup,
readiness, and advertised models before rebuilding the next member. This is
the rollout plan only; it does not execute SQL or automation.

## Prisma without a database

`prisma` was originally installed into the venv while the proxy was DB-less,
for a reason unrelated to databases: litellm[proxy] no longer pulls it, and
LiteLLM's auth-error handler unconditionally imports it to classify DB
outages — without it, a rejected or absent API key raised
`ModuleNotFoundError` and returned 500 instead of 401. Its presence was never
evidence that DB mode was intended, and the dependency is still required when
no database is configured.

## Same contract elsewhere

Virtual-key fields have separate ownership rules. `tasks/seed-keys.yml`
creates a key only when absent. The policy reconciler then replaces its model
allowlist with the rendered registry-derived list and replaces its MCP
permissions on every converge. A seed entry can set
`models_authoritative: false` to preserve an intentional additive model
extension. In `initial` mode, routes and attribution metadata remain
UI-owned; `rebuild` reconciles those fields. The budget reconciler remains
rebuild-gated and sends budget fields without `models`.

Role deployments (`tasks/seed-roles.yml`) carry the same `llm_router_seed_mode`
gate directly, not just the same contract by convention: in `initial` mode a
role is created once and then left alone (target and fallback order become
Admin-UI-owned), the same "database owns it after first seed" shape. In
`rebuild` mode every declared role is deleted and recreated with its current
`litellm_params` (including the timeout/window clamps to the local admission
bounds) and its declared fallback list pushed via `/fallback` — a full
re-seed of the role layer, run alongside the `router_settings` overwrite so a
DR reset restores git's role targets, fallbacks, and router settings in one
converge.

## Redis spend-tracking details

`redis_host`/`redis_password` resolve through `os.environ/`, like every other
secret in this config; `redis_port` renders as a literal int instead, because
LiteLLM's documented Redis examples type it that way and an unresolved
`os.environ/` marker where an int is expected risks failing at client
construction — the port is not a secret either, so routing it through the
EnvironmentFile bought nothing.

Deliberately absent: `fail_closed_budget_enforcement`. It governs LiteLLM's
Postgres-backed virtual-key budgets, not the provider budget above, and 503s
when spend can't be verified against Redis or a database.

The proxy now **has** a database (see `defaults/main/45-database.yml`) and
**issues virtual keys** (`defaults/main/56-virtual-keys.yml`), most with
budgets. The benchmark key is intentionally unbudgeted. Fail-closed mode stays
absent because it trades serving availability for strict accounting during a
Redis/database outage; current budgeted keys use LiteLLM's normal store fallback
behavior instead.
