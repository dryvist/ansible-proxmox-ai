# App-agent matrix

Generated from `llm_router_app_agent_tiers`; a test fails when this file drifts.

## vikunja-read

- App: vikunja
- Tier: read
- MCP server: vikunja_read
- OpenBao token field: VIKUNJA_AGENT_READ_TOKEN
- Allowed tools:
  - vikunja_tasks
  - vikunja_projects
  - vikunja_labels
  - vikunja_filters

## vikunja-write

- App: vikunja
- Tier: write
- MCP server: vikunja_write
- OpenBao token field: VIKUNJA_MCP_TOKEN_RW
- Allowed tools:
  - vikunja_tasks
  - vikunja_projects
  - vikunja_labels
  - vikunja_filters

## zammad-write

- App: zammad
- Tier: write
- MCP server: zammad_write
- OpenBao token field: ZAMMAD_AGENT_WRITE_TOKEN
- Allowed tools:
  - zammad_get_current_user
  - zammad_search_tickets
  - zammad_get_ticket
  - zammad_create_ticket
  - zammad_update_ticket
  - zammad_add_article
  - zammad_add_ticket_tag
