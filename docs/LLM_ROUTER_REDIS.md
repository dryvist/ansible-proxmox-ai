# LLM router shared store (Redis)

The router's shared store backs, whenever `llm_router_redis_host` is set:

| Feature | Setting | Store down |
| --- | --- | --- |
| Cooldowns, deployment rpm/tpm, provider budget | `router_settings.redis_*` | per-router in-memory counts |
| Key/team/user rate limits, spend counters | coordination store (from `litellm_settings.cache`) | per-router in-memory counts |
| Response cache, opt-in per request (`cache: {"use-cache": true}`) | `cache_params.mode: default_off`, `ttl: llm_router_response_cache_ttl` | cache miss |
| Virtual-key lookups | `enable_redis_auth_cache` | database lookup |
| Database spend writes | `general_settings.use_redis_transaction_buffer` | spend queued in memory, written once the store returns |
| Background health checks | `general_settings.use_shared_health_check` | each router checks on its own |

No feature refuses a request because the store is unreachable.
