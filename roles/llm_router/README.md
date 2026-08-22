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

## The model registry (`llm-models.yml`, repo root)

**Every model name, alias, tier and enabled/servable state is written once — in
the repo-root `llm-models.yml` registry — and this role is a projection of it.**
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
when spend can't be verified against Redis or a database — this proxy issues
no virtual keys and has no database, so it would be inert at best and a 503
generator on the fabric's only front door at worst.

## Model role aliases

Each physical backend has exactly one `model_list` deployment. Stable
consumer-facing role names are declared in the registry entry they point at
(`stable_aliases`), collected into `llm_router_model_group_aliases`, and rendered
as LiteLLM `router_settings.model_group_alias`. An alias carries no context
window, endpoint, or sampling configuration of its own, so changing the physical
model does not duplicate deployment settings — and because an alias is written
inside the entry it names, it cannot point at a model that is not in the
registry.

## Observability

`litellm_settings.callbacks: ["otel"]`:

- **OTLP/HTTP** traces to the Cribl Edge collector
  (`http://cribl-edge.<subdomain>:<otel_traces_http>/v1/traces`).

`/health/liveliness` is unauthenticated by design (LiteLLM load-balancer probe), so
Traefik health checks need no credential.

## Key variables (`defaults/main.yml`)

| Var | Default | Purpose |
| --- | --- | --- |
| `llm_router_registry_file` | repo-root `llm-models.yml` | the model registry every model var projects from |
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
- `prisma` is installed into the venv even though the proxy is DB-less:
  litellm[proxy] no longer pulls it, and LiteLLM's auth-error handler
  unconditionally imports it to classify DB outages — without it, a rejected or
  absent API key raised `ModuleNotFoundError` and returned 500 instead of 401.

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
