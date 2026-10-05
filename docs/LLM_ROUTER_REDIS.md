# LLM router shared store (Redis)

The router uses separate logical databases whenever `llm_router_redis_host` is
set. LiteLLM's router client is on `llm_router_redis_db`; its shared cache
client is on `llm_router_cache_redis_db`. The response cache and spend
coordination share that native cache client and database; their LiteLLM key
namespaces remain separate. The fast-subagent lock callback was retired before
this change, so this change adds no lock client or key and cannot alter its
semantics.

| Feature | Setting | Store down |
| --- | --- | --- |
| Cooldowns, deployment rpm/tpm, provider budget | `router_settings.redis_*` (logical DB 1) | per-router in-memory counts |
| Key/team/user limits, spend counters and DB spend buffer | `litellm_settings.cache` coordination store (logical DB 2) | per-router in-memory counts |
| Response cache (per-request opt-in) | `cache_params.mode: default_off`, `ttl: llm_router_response_cache_ttl` (logical DB 2) | cache miss |
| Virtual-key lookups | `enable_redis_auth_cache` | database lookup |
| Database spend writes | `general_settings.use_redis_transaction_buffer` | spend queued in memory, written once the store returns |
| Background health checks | `general_settings.use_shared_health_check` | each router checks on its own |

No feature refuses a request because the store is unreachable.

LiteLLM's native cache client owns distinct keys for response caching and spend
coordination. Router accounting and cooldown keys use the other logical DB.
