# Router observability and the serving-share metric

What the router records, which record may be measured against, and how the
local-versus-remote serving share is computed from it. Written down once so it
is not re-derived differently each time someone asks, and split out of
`roles/llm_router/README.md` for the reason that file's per-file token budget
exists: a reader changing a role setting should not have to read a measurement
definition to find it, and a reader computing the metric should not have to read
the role.

## Health endpoints

`/health/liveliness` is unauthenticated by design — it is LiteLLM's
load-balancer probe — so Traefik health checks need no credential.

## Two paths, one source of truth

**The request log is the measurement of record.** With a database attached, the
proxy writes one `LiteLLM_SpendLogs` row per request carrying `model` and
`model_group` (the name the caller asked for), `model_id` and `api_base` (the
deployment actually selected), `status`, token counts, `request_duration_ms`,
and `metadata.user_api_key_alias`. Nothing else in this fabric records which
deployment served a request, which is why the metric below is defined against
this table and not against traces. The settings that arm it, and the reasoning
for each: `roles/llm_router/defaults/main/45-database.yml`.

**OTLP traces are a convenience, not a source of truth.**
`litellm_settings.callbacks: ["otel"]` exports spans over OTLP/HTTP to the
shared collector named by `ai_orchestration_otel_endpoint`
(`inventory/group_vars/all.yml`), which fans them out to the tracing backends.

That export has been failing continuously, as span-batch export timeouts, and a
dropped span leaves no trace of itself by definition — which is why the gap went
unnoticed. **Do not build a measurement on it until it is proven to deliver.**

What has been ruled out, and what remains, so the next person does not re-check
the same things:

- **The endpoint composition is correct.** LiteLLM hands `OTEL_ENDPOINT` to the
  exporter verbatim and appends no signal path of its own, so the `/v1/traces`
  suffix the EnvironmentFile adds is required rather than doubled.
- **The batch bounds are unset and inherited.** LiteLLM constructs its
  `BatchSpanProcessor` with no explicit arguments, so it takes the OpenTelemetry
  SDK defaults unless the standard environment variables
  (`OTEL_BSP_MAX_EXPORT_BATCH_SIZE`, `OTEL_BSP_EXPORT_TIMEOUT`,
  `OTEL_BSP_SCHEDULE_DELAY`) are set. Those would belong in the router's
  EnvironmentFile.
- **The receiver's own capacity belongs to the collector's repository.**

This role declares the callback and the endpoint and cannot fix either of the
remaining two.

## Definition

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
- **`api_base`, not the model name.** A model name can be aliased, re-pointed,
  or shared between a local and a remote deployment; the address actually
  dialled cannot.

A share alone is easy to improve dishonestly — refusing remote work raises it.
Two companions are reported beside it and make it honest: the share each remote
tier carried, and the rate of rate-limit responses actually **returned to a
caller**. Retries that eventually succeeded are not in the latter; only the
final outcome reaches the failure path that writes a row.

## Queries

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

## Identifying a fallback: what does not work, and why

"Did a fallback chain run for this request" is a question this fabric has spent
real effort on, so the answer is recorded here rather than re-derived.

**The spend-log row cannot answer it, and the obvious reading of the columns is
a trap.** It is tempting to treat `model` as the name asked for and
`model_group` as the deployment selected, and call a difference a fallback. That
is wrong in both directions, verified against the pinned distribution:

- `model` is the **selected** deployment's upstream model string, reconstructed
  with its provider prefix. It is not the caller's requested name.
- `model_group` is the public name of the **selected** deployment too. On a
  fallback the router overwrites the request's `model_group` with the fallback
  target before the retry, so the row records where the request landed and not
  where it started.
- The two therefore differ on essentially every row by construction, fallback or
  not. A query built on that comparison reports a fallback rate near 100%.
- The router does track the chain as `previous_models`, but the spend-log
  metadata is built from a fixed allowlist that does not include it, so it never
  reaches the database.
- `routing_decision` in that allowlist is auto-router provenance (complexity,
  adaptive and quality strategies) and says nothing about fallbacks.
- `attempted_retries` counts retries **within** one group and is reset when a
  fallback enters the next one, so it is not a chain depth.

**What can answer it.** The router emits dedicated success and failure fallback
events carrying the original model group alongside the one that served. In the
pinned distribution exactly one shipped consumer implements them, the Prometheus
integration, which turns each into a labelled counter recording the requested
model, the model that served, the caller's key alias and the status code that
triggered the fallback. That is queryable, survives any logging change, and
costs one counter increment per fallback rather than per request. Per-request
attribution would need a callback that writes the same event to the database,
which is code this repository does not currently deploy.

**The router's own log cannot be raised to informational by the usual knobs.**
Its three named loggers are left at their inherited level, which passes warnings
and errors and drops informational records — which is why the routing trail is
absent while error output is plentiful. `LITELLM_LOG` sets the **handler**
threshold only and cannot recover a record the logger already dropped, and the
supported verbosity switches jump straight to debug for every logger at once.
The targeted route is a logging configuration file naming the router logger
specifically, passed to the server at startup.

## Known limits of the measurement

- **Caller attribution is partial, and the share does not depend on it.**
  `metadata.user_api_key_alias` is populated only for callers holding a virtual
  key (`roles/llm_router/defaults/main/56-virtual-keys.yml`). Everything still
  on the master key shares one hashed `api_key` value, so those requests are
  counted but not separated. Splitting the share *by caller* needs that closed
  first.
- **The window is bounded by retention.** Rows older than the retention period
  in `45-database.yml` are deleted by the proxy's own cleanup job, so a query
  reaching further back returns a partial period rather than an error. Widening
  the reporting window means widening retention first.
