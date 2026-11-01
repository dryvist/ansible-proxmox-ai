# Brain model selection

Hermes selects stable router aliases. Physical model IDs and deployment
settings remain in the central router/MLX catalogs. The older runtime
brain-sync implementation is retained but disabled.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_brain_sync_enabled` | `false` | Retain the optional brain-sync implementation without running it |
| `hermes_agent_brain_sync_interval` | `5min` | Poll cadence (`OnUnitActiveSec`) |
| `hermes_agent_brain_sync_bao_path` | `ai/public/brain` | KV v2 data path (mount `secret`) |
| `hermes_agent_brain_sync_bao_field` | `active_model` | Field holding the candidate model id |
| `hermes_agent_brain_sync_state_file` | `/etc/hermes-brain-sync/current-model` | Brain-sync's live pointer; watchdog probes the alias, not this |

## Live docs (Context7)

Registers Context7's hosted HTTP MCP server (`mcp_servers.context7`) so Hermes
can pull **current, version-specific library/framework docs** on demand instead
of relying on stale training data. The API key is referenced as
`${CONTEXT7_API_KEY}` (resolved from `.env`), bao-first (`secret/ai/hermes`) with
env fallback; the entry is omitted until the key is set.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_context7_mcp_enabled` | `true` | Register the Context7 MCP server |
| `hermes_agent_context7_api_key` | `""` | Context7 API key (bao/env) |

## Docs RAG search

Registers the shared agentgateway `/docs` route (`mcp_servers.docs`) — a
read-only `search_docs` tool over the `homelab_docs` Qdrant collection, which
the `llamaindex` role rebuilds nightly from `docs.jacobpevans.com`,
`docs.dryvist.com`, and the tofu service registry (see
`llamaindex_sources`/`llamaindex_index_on_calendar` in that role's defaults).
Keyless for the caller — the gateway's `mcp-docs` sidecar (`agentgateway_docker`
role) holds the LLM-router credential used to embed each query. Defaults ON in
both the default profile's `config.yaml` and every named profile that lists
`docs` in its `mcp` (currently both `splunk-admin` and `homelab-admin`) —
this is the estate's only document-retrieval index, so it belongs to every
profile doing homelab work.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_docs_mcp_enabled` | `true` | Register the docs MCP server |
| `hermes_agent_docs_mcp_url` | `mcp.<sub>/docs` | Agentgateway route (non-secret) |

OpenRouter uses one provider-level `OPENROUTER_API_KEY`. The `llm_router` role
constrains access with exact registry model ids and provider policy; additional
model-specific keys are neither required nor supported. Hermes normally asks
for `hermes-default`, whose local complexity route can reach the credentialed
cloud provider groups only after local serving fails. Explicit OpenRouter ids
remain available to callers that deliberately request an allowlisted model.
