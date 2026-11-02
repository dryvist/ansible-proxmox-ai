---
type: reference
title: Vikunja agent knowledge
description: Vikunja API behavior and app-token boundaries for router agents.
---

## Search and duplicate prevention

- Search tasks with `GET /tasks?s=<text>`. `GET /tasks/all` returns error 2004.
- Search before creating a task. Do not create a duplicate.

## Token scope

Vikunja API tokens are limited by route group. The read token grants only `read_*` actions on
tasks, projects, labels, filters, comments, relations, assignees and views. It cannot create,
update or delete data.

The Vikunja MCP package groups several operations under entity tools such as `vikunja_tasks`
and `vikunja_projects`. The read tier's tool allowlist cannot tell those subcommands apart; the
read-scoped app token enforces the write denial.

## Kanban views

Read a project's views with `GET /projects/<id>/views`, then read a view's tasks with
`GET /projects/<id>/views/<view>/tasks`.
