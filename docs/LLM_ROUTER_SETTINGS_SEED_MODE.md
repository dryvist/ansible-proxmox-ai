# llm_router router_settings seed mode: the litellm==1.102.0 facts

Split out of `roles/llm_router/README.md` ("Editing ladders in the UI") to
keep both files under the repo's per-file token budget
(`.token-limits.yaml`). Verified against the pinned `litellm==1.102.0` wheel,
never guessed. (Re-verified on the 1.98.0 → 1.102.0 bump: same mechanism
throughout, function/route names unchanged, only line numbers moved.)

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
  - New in this range (additive, not consulted by this role's converge):
    `management_endpoints/router_settings_endpoints.py` adds a Key > Team
    hierarchical `router_settings` lookup ahead of the global one this doc
    describes — irrelevant here since this proxy issues no virtual keys with
    their own `router_settings` (see "Redis spend-tracking details" below).

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

The database shares the ai-VLAN cluster that backs Hindsight, which already
carries the estate's DR standard.

## Schema updates: `db push` (ansible), never `migrate deploy` (litellm)

`main.yml` runs `prisma db push` as the service user before every restart —
idempotent, and the only path that has ever touched this schema. LiteLLM's
own startup also tries to manage the schema, and its default there is
different: `proxy_cli.py` calls `PrismaManager.setup_database(use_migrate=not
use_prisma_db_push, ...)` with `use_prisma_db_push` defaulting `False` (a
CLI-only flag with no env var), so an unmodified `litellm` boot always
attempts `prisma migrate deploy`. Against a schema this role has only ever
`db push`ed — no `_prisma_migrations` history — that fails `P3005` (schema
not empty), and litellm's own recovery path then tries to create a baseline
migration inside its installed package directory
(`litellm_proxy_extras/migrations/0_init`), which is root-owned like every
other pip-installed path, so the service user's write fails with
`PermissionError` and the proxy crash-loops (ai #845, litellm 1.98.0 →
1.102.0).

The fix is `DISABLE_SCHEMA_UPDATE=True` in the rendered env file
(`litellm.env.j2`, gated on `llm_router_store_model_in_db` like the `db push`
task itself): `should_update_prisma_schema()` then returns `False` and
startup takes the `check_prisma_schema_diff()` branch instead, which only
logs a diff (never raises) and leaves schema management entirely to the
`db push` task that already ran. Verified against the pinned
`litellm==1.102.0` wheel (`litellm/proxy/proxy_cli.py`,
`litellm/proxy/db/prisma_client.py`, `litellm/proxy/db/check_migration.py`).

## Prisma without a database

`prisma` was originally installed into the venv while the proxy was DB-less,
for a reason unrelated to databases: litellm[proxy] no longer pulls it, and
LiteLLM's auth-error handler unconditionally imports it to classify DB
outages — without it, a rejected or absent API key raised
`ModuleNotFoundError` and returned 500 instead of 401. Its presence was never
evidence that DB mode was intended, and the dependency is still required when
no database is configured.

## Same contract elsewhere

Role deployments and Virtual Keys already carry the same "database owns it
after first seed" contract `llm_router_seed_mode` extends to
`router_settings`: `tasks/seed-keys.yml` mints a key only when absent, then
only ever adds model names to an existing key's scope; `tasks/
reconcile-key-budgets.yml` never sends `models`, only budget fields.

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

The proxy now **has** a database (see `defaults/main/45-database.yml`), so that
is no longer the reason. The reason is the other one, and it still stands: this
proxy **issues no virtual keys**, so there are no key budgets to enforce and the
setting would be inert at best, a 503 generator on the fabric's only front door
at worst. Reconsider it when virtual keys are issued, not before.
