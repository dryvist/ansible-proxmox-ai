---
type: reference
title: Zammad agent knowledge
description: Zammad ticket, search, identity, and tagging requirements for router agents.
---

## Tickets and identity

- Creating a ticket requires a customer. Resolve the current user with `GET /api/v1/users/me`
  and pass that user explicitly as `customer` when creating the ticket.
- Internal notes are articles with `internal: true`.
- A transient 401 can occur during token rotation. Retry up to three times before diagnosing an
  authorization failure.

## Search

- Use `created_at:[now-2d TO *]` for a recent-time range. `created_at:>now-24h` does not work.
- Filter tags with `tags:"<tag>"`.
- `/api/v1/overviews` is admin-scoped; agent tokens are refused.

## Ticket tags

Tag every ticket from the controlled vocabulary:

- the applicable system tags;
- one class tag, such as `outage`, `degradation`, `config-drift`, `auth`, `network`,
  `monitoring-gap` or `llm-serving`;
- the applicable series tags.

Do not invent tags outside those vocabularies.
