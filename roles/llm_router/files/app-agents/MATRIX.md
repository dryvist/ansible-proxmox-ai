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
- OpenBao token field: VIKUNJA_AGENT_WRITE_TOKEN
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

## youtrack-write

- App: youtrack
- Tier: write
- MCP server: youtrack_write
- OpenBao token field: YOUTRACK_AGENT_WRITE_TOKEN
- Transport: HTTP
- Allowed tools:
  - search_issues
  - get_issue
  - get_issue_comments
  - add_issue_comment
  - get_issue_fields_schema
  - find_projects
  - get_project
  - find_user
  - link_issues
  - manage_issue_tags
  - log_work
  - get_saved_issue_searches
  - get_current_user
  - search_articles
  - get_article

## semaphore-write

- App: semaphore
- Tier: write
- MCP server: semaphore_write
- OpenBao token field: SEMAPHORE_AGENT_WRITE_TOKEN
- Allowed tools:
  - list_projects
  - list_templates
  - get_template
  - list_tasks
  - filter_tasks
  - get_task
  - get_task_raw_output
  - get_waiting_tasks
  - get_latest_failed_task
  - analyze_task_failure
  - run_task
  - stop_task
