# LiteLLM MCP gateway

Split out of `roles/llm_router/README.md` to keep both files under the
repository's per-file token budget (`.token-limits.yaml`).

The router starts pinned stdio MCP packages with service credentials supplied
through its root-only systemd environment file. The selected stdio servers do
not support per-user OAuth delegation, so credentials are shared at the server
process and virtual-key permissions control which callers can reach each
server. Every managed key defaults to no MCP servers; only Hermes profile keys
receive the Zammad and Slack grants. Server allowlists expose only ticket
search/read and public-channel list/history/thread tools.

The Slack preflight checks that the existing bot grant includes all required
read scopes and derives the workspace ID from `auth.test`. The Zammad service
token and URL come from the existing OpenBao domain data. Check both service
credentials and their service-side access before enabling the gateway.

The package pins match the shared MCP catalog: Zammad MCP `1.1.0`, the MCP SDK
bound `mcp<2`, and Slack MCP `2025.4.25`.
