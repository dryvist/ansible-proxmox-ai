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

## Escalation (Codex via MCP)

Registers `codex mcp-server` (OpenAI's Codex CLI) as an MCP tool
(`mcp_servers.codex`) — a deliberate escalation path for problems worth a
stronger model, or a session that's stuck/looping. This is **not** automatic
on-error fallback (Hermes' own `fallback_providers` feature is intentionally
unused here); tool use is inherently a per-call, model-chosen decision, so
the agent reaches for Codex only when it judges the problem warrants it, the
same way it decides whether to call any other tool.

Codex runs under a completely separate, low-privilege OS user —
`codex-runner`, provisioned by the sibling `codex_runner` role on the same
host — never as `hermes`. The MCP entry invokes it through a single-command
`sudo` grant (`hermes` → `codex-runner`, exactly `codex mcp-server`, nothing
else); Hermes never gains filesystem access to that user's ChatGPT-OAuth
credential, so the token itself is not directly readable by the agent even
though the agent can fully use the tool.

Codex's OAuth login is a manual, one-time, interactive step that cannot be
automated by Ansible — see `roles/codex_runner/README.md` for both bootstrap
options (fresh `codex login`, or copying an already-authenticated
`~/.codex/auth.json`). Until that's done, the MCP entry is present but every
call to it errors; the daemon itself starts and runs normally regardless.

OpenRouter uses one provider-level `OPENROUTER_API_KEY`. The `llm_router` role
constrains access with exact registry model ids and provider policy; additional
model-specific keys are neither required nor supported. Hermes normally asks
for `hermes-default`, whose local complexity route can reach the credentialed
cloud provider groups only after local serving fails. Explicit OpenRouter ids
remain available to callers that deliberately request an allowlisted model.

| Variable | Default | Meaning |
| --- | --- | --- |
| `hermes_agent_codex_mcp_enabled` | `true` | Register the Codex MCP server |
