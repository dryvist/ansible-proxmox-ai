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
(`llm_router_primary_model` / `_small_model` / `_cluster_model`), the servable
set and the alias map from that file; `templates/config.yaml.j2` renders the
LiteLLM config from those views. Nothing in the role re-types a model id, and a
test fails the build if anything starts to.

Each entry keeps three names distinct on purpose — `client_model_id` (what a
caller sends), the provider-prefixed LiteLLM route string, and
`upstream_model_id` (what the backend serves) — so a rename on one side is never
silently a rename on the others. Topology stays here, not there: the registry
selects a backend symbolically (`tier`, and `endpoint: cluster`), and this role
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
| Add an OpenRouter model | one registry entry + seed its `key_field` |
| Retire a model | `enabled: false` (or delete the entry) |

## Tiers (one proxy, two backends)

The router registers every physical backend exactly once. Consumers may request
a physical ID or a stable role from `llm_router_model_group_aliases`.

| Model ids | Backend | Auth |
| --- | --- | --- |
| `mlx-community/*` large models (`Qwen3.6-35B-A3B-OptiQ-4bit`, `gpt-oss-120b-MXFP4-Q8`, …) | `llm-large` runner (`/v1`, bearer) | `LLM_LARGE_BEARER_TOKEN` |
| `qwen3-4b`, `embeddings` | `llm-fast` (GPU) **and** `llm-light` (CPU) | none |
| `nvidia/nemotron-3-ultra-550b-a55b:free` (extensible list) | OpenRouter (paid-SaaS egress) | one key **per model** |

Each light model id is registered as **two deployments** with the same `model_name`
(the GPU `llm-fast` box and the CPU `llm-light` standby). LiteLLM load-balances the
pair and cools a failed deployment down (`allowed_fails` / `cooldown_time`), so a GPU
outage drains to CPU automatically. There is **no** cross-tier fallback — a large
request that fails surfaces the error rather than silently degrading to a small model.

## OpenRouter egress tier (optional, per-model keys)

Registry entries with `tier: openrouter` register OpenRouter-hosted models under
their real upstream ids. Deliberate properties:

- **One OpenRouter API key per MODEL** (never per harness/caller). Each entry's
  `key_field` names its field in the OpenBao paid-SaaS key area, canonically
  `secrets-external/ai/saas/openrouter` (an internet-reachable SaaS
  credential; dual-mounted with the internal `secret/ai/saas/openrouter`
  path during the migration); the `openbao_secrets` pre-play delivers it via
  the `ai-saas-openrouter` policy leaf, and the rendered EnvironmentFile
  carries it as `OPENROUTER_API_KEY_<KEY_FIELD upper-snaked>`.
- **Inert until seeded** — an entry whose key is absent renders nothing, so
  the list is safe to extend before the key exists.
- **Opt-in only** — OpenRouter models are never chained into a fallback;
  consumers (Hermes, Open WebUI, workstation harnesses) must name the real
  upstream id to reach the SaaS egress.

Seeding a new model (operator, once per model): mint a scoped key in the
OpenRouter console, then
`bao kv patch secrets-external/ai/saas/openrouter <model-slug>=<key>` and
re-converge this role. The first entry is `nvidia/nemotron-3-ultra-550b-a55b:free`
(rate-limited; NVIDIA logs prompts on the `:free` endpoint — never send
confidential material through it).

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
