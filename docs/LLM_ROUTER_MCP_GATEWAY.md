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

The Slack server is optional. The Zammad URL and token must resolve or the
converge fails. The Slack server, its environment lines, its workspace lookup,
its post-restart tools check and its key grant (`llm_router_mcp_slack_enabled`)
exist only while `SLACK_BOT_TOKEN` resolves non-empty in the local-llm secrets
domain. With it empty the converge prints one notice and still passes, and
Hermes profile keys receive the Zammad grant alone.

The Slack preflight (when enabled) checks that the existing bot grant includes all required
read scopes and derives the workspace ID from `auth.test`. The Zammad service
token and URL come from the existing OpenBao domain data. Check both service
credentials and their service-side access before enabling the gateway.

The package pins match the shared MCP catalog: Zammad MCP `1.1.0`, the MCP SDK
bound `mcp<2`, and Slack MCP `2025.4.25`.
