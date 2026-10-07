# Tiers (one proxy, multiple backends)

The router registers every physical backend exactly once. Consumers may request
a physical ID or a stable role from `llm_router_model_group_aliases`.

| Model ids | Backend | Auth |
| --- | --- | --- |
| `mlx-community/*` large models (`Qwen3.6-35B-A3B-OptiQ-4bit`, `gpt-oss-120b-MXFP4-Q8`, …) | `llm-large` runner (`/v1`, bearer) | `LLM_LARGE_BEARER_TOKEN` |
| `qwen3-4b`, `embeddings` | `llm-light` (CPU), plus `llm-fast` (GPU) when `llm_router_llm_fast_enabled` | none |
| Disabled `gpu-*` candidates | first `llm_gpu_group` host from tofu tag `llm-gpu`; set by the router group vars once a host exists | none |
| OpenRouter allowlisted ids | OpenRouter (paid-SaaS egress) | one provider key |
| `hermes-default` | local complexity router with credential-gated provider fallbacks | one key per API provider |

Each light model id is registered as a CPU `llm-light` deployment, and as a second
same-`model_name` GPU `llm-fast` deployment **only when `llm_router_llm_fast_enabled`
is true**. With that toggle false the tier is a single deployment per model name and
there is no standby. When both are registered, LiteLLM load-balances the pair and
cools a failed deployment down (`allowed_fails` / `cooldown_time`), so a GPU outage
drains to CPU. There is **no** cross-tier fallback — a large
request that fails surfaces the error rather than silently degrading to a small model.

The Pro6000 router candidates stay disabled and unservable in
`llm-models.d/60-gpu-pro6000.yml`. Engine profiles remain available for an
explicit engine selection; only the inventory-selected profile is activated.
For the router pool, `inventory/group_vars/llm_router_group.yml` sets
`llm_router_gpu_profiles_enabled` to true exactly when `llm_gpu_group` has a
host, and only the active profile (`llm_active_profile`) is projected.
`playbooks/llm-serving.yml` converges that guest with the `llm_gpu_serving` tag
before the router play. While the switch is off, `judge` and `subagent` resolve
to the current routine and 4080 tiers, and the existing `best`, `lead`, and
`long` aliases keep their current targets. Each rendered profile carries zero
token cost and advertises `max_input_tokens` equal to its registry
`context_window`, which is the engine's `max_model_len` contract. The vLLM
sweep candidates admit up to 64 requests for load testing; `max_num_seqs`
remains the engine's active sequence-slot limit. The benchmark campaign selects
measured model ids and limits before any candidate is enabled or servable.

Hermes sends `hermes_agent_model` from its default and named profile configs.
The default key retains its configured model scope. Each named profile key
also includes every active registry-owned `gpu-*` alias, so Hermes can resolve
those aliases without the human-chat role scope. The aliases disappear from
the key while GPU profiles are inactive; `test_hermes_profile_keys_assert.yml`
and `test_hermes_agents.yml` enforce the rendered contract.
