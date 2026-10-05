# Tiers (one proxy, multiple backends)

The router registers every physical backend exactly once. Consumers may request
a physical ID or a stable role from `llm_router_model_group_aliases`.

| Model ids | Backend | Auth |
| --- | --- | --- |
| `mlx-community/*` large models (`Qwen3.6-35B-A3B-OptiQ-4bit`, `gpt-oss-120b-MXFP4-Q8`, …) | `llm-large` runner (`/v1`, bearer) | `LLM_LARGE_BEARER_TOKEN` |
| `qwen3-4b`, `embeddings` | `llm-light` (CPU), plus `llm-fast` (GPU) when `llm_router_llm_fast_enabled` | none |
| Four disabled `gpu-*` candidates | first `llm_gpu_group` host from tofu tag `llm-gpu`; requires `llm_router_gpu_profiles_enabled: true` | none |
| OpenRouter allowlisted ids | OpenRouter (paid-SaaS egress) | one provider key |
| `hermes-default` | local complexity router with credential-gated provider fallbacks | one key per API provider |

Each light model id is registered as a CPU `llm-light` deployment, and as a second
same-`model_name` GPU `llm-fast` deployment **only when `llm_router_llm_fast_enabled`
is true**. With that toggle false the tier is a single deployment per model name and
there is no standby. When both are registered, LiteLLM load-balances the pair and
cools a failed deployment down (`allowed_fails` / `cooldown_time`), so a GPU outage
drains to CPU. There is **no** cross-tier fallback — a large
request that fails surfaces the error rather than silently degrading to a small model.

The Pro6000 profiles are benchmark placeholders and stay disabled by default,
even if an inventory group appears. A later profile-switch change must flip
`llm_router_gpu_profiles_enabled` only after the serving guest is ready. While
the switch is off, `judge` and `subagent` resolve to the current routine and
4080 tiers, and the existing `best`, `lead`, and `long` aliases keep their
current targets. Each rendered profile carries zero token cost and advertises
`max_input_tokens` equal to its registry `context_window`, which is the engine's
`max_model_len` contract. The benchmark campaign finalises all four model ids
and limits.
