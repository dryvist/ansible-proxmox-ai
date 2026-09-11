# llm_router

Deploys a [LiteLLM](https://github.com/BerriAI/litellm) proxy — the LLM fabric's
single OpenAI-compatible front door — on each guest tagged `llm-router`. Native
install: a Python venv + systemd, config at `/etc/litellm/config.yaml`. Fronted by
Traefik at `https://llm.<subdomain>`; consumers select a tier purely by model alias,
so the backend topology is swappable with no app change.

## Installation

Ships with the `ansible-proxmox-apps` repo; no external install. Wired into
`playbooks/site.yml` against `llm_router_group` (guests tagged `llm-router` in the
tofu inventory). Tools come from the repo's Nix dev shell (`direnv allow`).

## The model registry (`llm-models.d/`, repo root)

**Every model name, alias, tier and enabled/servable state is written once — in
the repo-root `llm-models.d/` registry — and this role is a projection of it.**
`defaults/main.yml` derives its tier views (`llm_router_large_models`,
`llm_router_light_models`, `llm_router_openrouter_models`), its selector vars
(`llm_router_primary_model` / `_small_model` / `_routine_model`), the servable
set and the alias map from that file; `templates/config.yaml.j2` renders the
LiteLLM config from those views. Nothing in the role re-types a model id, and a
test fails the build if anything starts to.

Each entry keeps three names distinct on purpose — `client_model_id` (what a
caller sends), the provider-prefixed LiteLLM route string, and
`upstream_model_id` (what the backend serves) — so a rename on one side is never
silently a rename on the others. Topology stays here, not there: the registry
selects a backend symbolically (`tier`), and this role
owns the URLs, ports and bearer env names. See the registry's own header for the
full field reference.

Two fields are easy to confuse and must not be: `enabled` means the router
offers the id at all, `servable` means the backend will actually answer for it.
The serving host runs llama-swap in single-model mode, so a non-servable id
returns HTTP 404 rather than a degraded answer.

Common edits:

| Change | Edit |
| --- | --- |
| Repoint the serving host | move `serving_role: primary` to another entry |
| Add/remove a consumer alias | that entry's `stable_aliases` |
| Add an OpenRouter model | one registry entry; reuse the provider credential |
| Repoint a role (`subagent`, `lead`, ...) | the Admin UI — not this file |
| Retire a model | `enabled: false` (or delete the entry) |

## Tiers (one proxy, two backends)

The router registers every physical backend exactly once. Consumers may request
a physical ID or a stable role from `llm_router_model_group_aliases`.

| Model ids | Backend | Auth |
| --- | --- | --- |
| `mlx-community/*` large models (`Qwen3.6-35B-A3B-OptiQ-4bit`, `gpt-oss-120b-MXFP4-Q8`, …) | `llm-large` runner (`/v1`, bearer) | `LLM_LARGE_BEARER_TOKEN` |
| `qwen3-4b`, `embeddings` | `llm-light` (CPU), plus `llm-fast` (GPU) when `llm_router_llm_fast_enabled` | none |
| OpenRouter allowlisted ids | OpenRouter (paid-SaaS egress) | one provider key |
| `hermes-default` | local complexity router with credential-gated provider fallbacks | one key per API provider |

Each light model id is registered as a CPU `llm-light` deployment, and as a second
same-`model_name` GPU `llm-fast` deployment **only when `llm_router_llm_fast_enabled`
is true**. With that toggle false the tier is a single deployment per model name and
there is no standby. When both are registered, LiteLLM load-balances the pair and
cools a failed deployment down (`allowed_fails` / `cooldown_time`), so a GPU outage
drains to CPU. There is **no** cross-tier fallback — a large
request that fails surfaces the error rather than silently degrading to a small model.

## OpenRouter egress tier (optional, one provider key)

Registry entries with `tier: openrouter` register OpenRouter-hosted models under
their real upstream ids. Deliberate properties:

- **One OpenRouter API key for the provider.** Every OpenRouter registry entry
  references `OPENROUTER_API_KEY`; exact model ids and provider policy enforce
  access rather than model-specific credentials.
- **Inert until seeded** — an entry whose key is absent renders nothing, so
  the list is safe to extend before the key exists.
- **Opt-in only** — OpenRouter models are never chained into a fallback;
  consumers (Hermes, Open WebUI, workstation harnesses) must name the real
  upstream id to reach the SaaS egress.

Seeding OpenRouter is a one-time provider operation: mint a dedicated LiteLLM
key with its provider-side spend limit, store it as `OPENROUTER_API_KEY`, and
re-converge this role. The first explicit entry is `nvidia/nemotron-3-ultra-550b-a55b:free`
(rate-limited; NVIDIA logs prompts on the `:free` endpoint — never send
confidential material through it).

## The vLLM tier and the Hermes local GPU leg

The `vllm`-tier loop renders one deployment per model — vLLM serves a single
model per instance and there is no standby serving the same weights, so this
tier deliberately does not render a pair. The same loop also carries any
`hermes-local` entries: distinct Hermes-fallback-chain rungs over that same
backend/URL/key, not a second physical tier. A `context_window` here is
mandatory rather than optional (unlike other tiers) because these backend ids
are absent from LiteLLM's catalog — an omitted value silently resolves
`max_input_tokens` to null, disables `enable_pre_call_checks` for the
deployment, and lets an over-long request through to a model that cannot hold
it (the compress-death outage class, 2026-07-08). `allowed_fails`/
`cooldown_time` overrides on a `hermes-local` entry exist because busy is not
unhealthy: a merely-busy single GPU must not be cooled out of rotation the way
a real failure would be. Its `num_retries: 0` is the same idea — a
single-instance local leg is only ever accepting or rejecting, never worth
retrying, since a retry just re-queues behind the same busy box.

## OpenRouter wildcard passthrough

The enumerated OpenRouter loop is no longer the sole egress allowlist: any
OpenRouter model is reachable by requesting `openrouter/<real-id>` directly
(`model_name: "openrouter/*"`, `config.yaml.j2`). This is a deliberate
reversal — read `defaults/main/30-openrouter.yml` before touching that block.

It is **not** reachable through any fallback chain: LiteLLM resolves a
fallback target by exact `model_name`, which skips wildcard rewriting and
would forward the literal `"*"` upstream. It **is** more specific than the
large-tier bare `"*"` (a longer pattern string ranks first in LiteLLM's
`PatternMatchRouter`), so an `"openrouter/..."` request reaches this
deployment and never the Mac gate. It carries no `max_budget`/`budget_duration`
— there is no per-model spend figure to attach, and no separate shared-spend
key for wildcard traffic distinct from the tier-wide Redis cap; that is a
known gap, not an oversight.

## Spend tracking (Redis)

`router_settings.redis_host`/`redis_port`/`redis_password` back LiteLLM's
provider spend tracking, and the `openrouter` `provider_budget_config` ceiling
renders **only** when Redis is configured (`tasks/assert.yml` fails the build
otherwise). Without a shared store, a multi-member pool would count only its
own spend, silently turning a stated ceiling into N times its real value and
resetting it on every rolling converge — a control that reports a limit it
does not hold is worse than an absent one.

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

## Model role aliases

Each physical backend has exactly one `model_list` deployment. Stable
consumer-facing role names are declared in the registry entry they point at
(`stable_aliases`), collected into `llm_router_model_group_aliases`, and rendered
as LiteLLM `router_settings.model_group_alias`. An alias carries no context
window, endpoint, or sampling configuration of its own, so changing the physical
model does not duplicate deployment settings — and because an alias is written
inside the entry it names, it cannot point at a model that is not in the
registry.

Only an alias on a **servable** entry renders this way. An alias declared
elsewhere — on an egress model, on a light-tier one — is a *role*, and roles
live in the database instead. See below.

## Roles: what lives in git, what lives in the database

Authority is split, and the split follows how often each half changes.

| | Lives in | Owned by | Changes |
| --- | --- | --- | --- |
| **Catalog** — which models exist, their cost, context, credentials | `llm-models.d/` → `config.yaml` | this role, per converge | rarely |
| **Roles** — which model a caller-facing name resolves to, and its fallback order | the router database | the Admin UI | often |

Callers name a role — `lead`, `subagent`, `judge`, `cheap`, `embed`, `ocr` —
never a physical model, so a routing change reaches every client at once with no
client edit and no release. The first seed is declared in
`defaults/main/55-roles.yml`; `tasks/seed-roles.yml` writes a role only when the
running proxy carries no deployment by that name, so **a converge never
overwrites an edit made in the UI**. Change a role in the UI, not in git —
editing the seed affects only a proxy that has never carried the role before.

Roles require the database (`llm_router_db_host`) and
`llm_router_store_model_in_db`; with neither, config-rendered aliases still work
and no role is seeded.

Two rules bind a seeded fallback rung, and a rung must satisfy both:

- **Largest context first.** A shorter-context fallback does not degrade a
  caller, it truncates one, and `enable_pre_call_checks` rejects the over-long
  request rather than routing it — so a short rung behind a long-context model
  is a hard failure exactly when it was meant to help.
- **Zero cost.** A role carrying delegated bulk work must not fall into
  metered egress: an outage would become spend nobody chose. Paid models stay
  reachable by name; they are never something a role falls into.

Where no catalogued model satisfies both, the role is seeded with no fallback
record at all. It then fails honestly rather than silently, and whoever owns
the UI adds a rung deliberately.

### Swap a role

In the Admin UI at `/ui` (sign-in from `UI_USERNAME` / `UI_PASSWORD`): **Models**
→ the role's row → change its target, or **Settings → Fallbacks** → reorder.
Effective immediately; no restart, no converge, no git diff.

The same two edits by hand, against the proxy with the master key:

```bash
curl -X POST "$ROUTER/fallback" \
  -H "Authorization: Bearer $LLM_ROUTER_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model": "subagent", "fallback_models": ["<first>", "<second>"], "fallback_type": "general"}'
```

## Admin UI SSO

`/ui` signs in via Authelia through LiteLLM's generic-OIDC environment. The
block renders only when the client secret resolves; env contract:
`defaults/main/65-oidc.yml`. Redirect target: `<PROXY_BASE_URL>/sso/callback`.
`PROXY_ADMIN_ID` is the operator email, matching the APPS authelia role's
`authelia_admin_email`.

The secret is bao-first (`secret/apps/authelia`) with `LITELLM_OIDC_CLIENT_SECRET`
env fallback, non-mandatory: until seeded the block stays absent and the
converge stays green with SSO off. `UI_USERNAME`/`UI_PASSWORD` remain the
`/fallback/login` break-glass. Boards link the router at `/ui` via the ingress
`url_path`, not the API root.

## Observability

Two independent paths, and only one of them is load-bearing for measurement.

**The request log is the measurement of record.** With a database attached, the
proxy writes one `LiteLLM_SpendLogs` row per request carrying `model` and
`model_group` (the name the caller asked for), `model_id` and `api_base` (the
deployment actually selected), `status`, token counts, `request_duration_ms`,
and `metadata.user_api_key_alias`. Nothing else in this fabric records which
deployment served a request, which is why the serving-share metric below is
defined against this table and not against traces. Retention, redaction and the
settings that arm it: `defaults/main/45-database.yml`.

**OTLP traces are a convenience, not a source of truth.** `litellm_settings.
callbacks: ["otel"]` exports spans over OTLP/HTTP to the shared collector named
by `ai_orchestration_otel_endpoint` (`inventory/group_vars/all.yml`), which fans
them out to the tracing backends. The endpoint composition here is correct —
LiteLLM passes `OTEL_ENDPOINT` to the exporter verbatim and appends no path, so
the `/v1/traces` suffix the EnvironmentFile adds is required, not doubled.

That export has nonetheless been failing continuously, as span-batch export
timeouts, and a dropped span leaves no trace of itself by definition. **Do not
build a measurement on it until it is proven to deliver.** The remaining
candidate causes are all outside this role: the batch-span-processor bounds are
standard OpenTelemetry SDK environment variables (`OTEL_BSP_MAX_EXPORT_BATCH_SIZE`,
`OTEL_BSP_EXPORT_TIMEOUT`) that would belong in the EnvironmentFile, and the
receiver's own capacity belongs to the collector's repository. This role
declares the callback and the endpoint; it cannot fix either.

`/health/liveliness` is unauthenticated by design (LiteLLM load-balancer probe), so
Traefik health checks need no credential.

### The serving-share metric

The program this proxy serves is stated as a share — most requests answered by
estate-hosted models, remote tiers only for overflow. Written down once, here,
so it is not re-derived differently each time someone asks:

> **`local_share`** = chat-completion requests answered by a deployment whose
> `api_base` is an estate-hosted address, divided by all chat-completion
> requests the proxy handled. Reported **weekly**.

Three deliberate choices in that wording, each of which changes the number:

- **Chat completions only.** `call_type` is `acompletion`/`completion`.
  Embeddings (`aembedding`) are served locally without exception, so counting
  them inflates the share in precisely the flattering direction.
- **The numerator counts successes**, because a request that failed was not
  answered by anything. The denominator counts every row, success or failure —
  a request the fabric could not serve is a request the fabric did not serve
  locally.
- **`api_base`, not the model name.** A model name can be aliased,
  re-pointed, or shared between a local and a remote deployment; the address
  actually dialled cannot.

A share alone is easy to improve dishonestly — refusing remote work raises it.
Two companions are reported beside it and make it honest: the share each remote
tier carried, and the rate of rate-limit responses actually **returned to a
caller**. Retries that eventually succeeded are not in the latter; only the
final outcome reaches the failure path that writes a row.

Run against the router's own database. `:estate_suffix` is a bind parameter
carrying the internal subdomain (the value behind `PROXMOX_SUBDOMAIN`), so the
query itself holds no address; in `psql`, `\set estate_suffix ...` and write it
as `:'estate_suffix'`.

```sql
-- Weekly serving share, remote share, and caller-visible rate limiting.
-- The suffix is matched with wildcards on BOTH sides: api_base is a full URL
-- and ends in a port, so an anchored suffix match silently returns zero local
-- requests — a broken query and a failing fabric look the same from here.
WITH reqs AS (
  SELECT
    date_trunc('week', "startTime") AS week,
    status,
    api_base LIKE '%' || :estate_suffix || '%' AS is_local,
    metadata -> 'error_information' ->> 'error_code' AS error_code
  FROM "LiteLLM_SpendLogs"
  WHERE call_type IN ('acompletion', 'completion')
    AND "startTime" >= now() - interval '28 days'
)
SELECT
  week,
  count(*) AS requests,
  round(count(*) FILTER (WHERE is_local AND status = 'success')::numeric
        / NULLIF(count(*), 0), 3) AS local_share,
  round(count(*) FILTER (WHERE NOT is_local AND status = 'success')::numeric
        / NULLIF(count(*), 0), 3) AS remote_share,
  round(count(*) FILTER (WHERE status = 'failure' AND error_code = '429')::numeric
        / NULLIF(count(*), 0), 3) AS caller_visible_429_rate
FROM reqs
GROUP BY week
ORDER BY week DESC;
```

```sql
-- Which tier carried the work, for the week just reported. Tier is derived
-- from the address first and the provider second: every local backend is
-- OpenAI-compatible, so custom_llm_provider alone labels the estate's own
-- models 'openai' and the split reads as fully remote.
SELECT
  CASE WHEN api_base LIKE '%' || :estate_suffix || '%' THEN 'local'
       ELSE COALESCE(NULLIF(custom_llm_provider, ''), 'unknown') END AS tier,
  count(*) FILTER (WHERE status = 'success') AS answered,
  count(*) FILTER (WHERE status = 'failure') AS failed,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY request_duration_ms) AS p50_ms
FROM "LiteLLM_SpendLogs"
WHERE call_type IN ('acompletion', 'completion')
  AND "startTime" >= date_trunc('week', now()) - interval '7 days'
  AND "startTime" <  date_trunc('week', now())
GROUP BY tier
ORDER BY answered DESC;
```

Caller attribution is partial today and the metric does not depend on it:
`metadata.user_api_key_alias` is populated only for callers holding a virtual
key (`defaults/main/56-virtual-keys.yml`). Everything still on the master key
shares one hashed `api_key` value, so those requests are counted but not
separated. Splitting the share *by caller* needs that gap closed first.

## Key variables (`defaults/main.yml`)

| Var | Default | Purpose |
| --- | --- | --- |
| `llm_router_registry_dir` | repo-root `llm-models.d/` | the model registry every model var projects from |
| `llm_router_api_port` | `service_ports.llm_router_api` | proxy listen port (no hardcode) |
| `llm_router_light_port` | `service_ports.llm_fast_api` | llm-fast / llm-light backend port |
| `llm_router_large_port` | `service_ports.ollama_api` | llm-large backend port |
| `llm_router_routing_strategy` | `simple-shuffle` | load-balancing across same-name deployments |
| `llm_router_master_key` | `env LLM_ROUTER_MASTER_KEY` (mandatory) | proxy master key |
| `llm_router_llm_large_bearer` | `env LLM_LARGE_BEARER_TOKEN` (mandatory) | llm-large bearer |

## Dependencies

- `tofu-proxmox` constants must expose `service_ports.llm_router_api` **and**
  `service_ports.llm_fast_api` (added by the parallel constants PR). Both are
  hard-required — a missing constant fails loud.
- Secrets `LLM_ROUTER_MASTER_KEY` + `LLM_LARGE_BEARER_TOKEN` are env-sourced
  (SOPS/Doppler) today; the OpenBao migration is a separate phase.
- `prisma` was originally installed into the venv while the proxy was DB-less,
  for a reason unrelated to databases: litellm[proxy] no longer pulls it, and
  LiteLLM's auth-error handler unconditionally imports it to classify DB
  outages — without it, a rejected or absent API key raised
  `ModuleNotFoundError` and returned 500 instead of 401. Its presence was never
  evidence that DB mode was intended, and the dependency is still required when
  no database is configured.

### Database (optional)

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
  truth while roles become editable — see "Roles" above.
- Spend and error logs are **on**, and bounded. They were off while the
  objection was unbounded per-request growth; LiteLLM's native retention job
  answers that, and Redis was never the substitute it was treated as — it holds
  running spend counters, not a per-request record, so it cannot say which
  deployment served a request. Retention is 30 days, prompts and responses are
  redacted out of every row, and enabling the logs without a retention period
  is fatal at template time rather than silently unbounded. Full reasoning:
  `defaults/main/45-database.yml`; what the rows are for: "Observability" above.
- Credentials are bao-first from `apps/llm-router`, the same field the Postgres
  converge in `ansible-proxmox-apps` uses to create the role, so the two ends
  cannot drift.

The database shares the ai-VLAN cluster that backs Hindsight, which already
carries the estate's DR standard.

## Usage

```bash
env -u DOPPLER_PROJECT -u DOPPLER_CONFIG -u DOPPLER_ENVIRONMENT doppler run -- \
  ./scripts/run-ansible.sh playbooks/site.yml --limit llm_router_group --tags llm_router,ai
```

## Not yet live-validated

Verify on the first converge: (a) `litellm[proxy]` + the `langfuse` / `otel`
callbacks import cleanly in the venv; (b) the `llm-large` runner accepts the bearer
on `/v1`; (c) the same-name GPU/CPU deployment pair drains as intended when the GPU
box is stopped.
