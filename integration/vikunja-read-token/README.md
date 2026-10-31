---
type: Integration Guide
title: Vikunja read-token sidecar fixture
description: Local acceptance test for read-only Vikunja API token enforcement through the role's actual MCP sidecar.
timestamp: 2026-10-31T00:00:00Z
---

## Scope

This fixture starts the official Vikunja **2.7.1** image pinned by digest in
`compose.yml`, using SQLite in an isolated temporary Docker project. The test
creates one synthetic local user, project, task, and two scoped API tokens via
Vikunja's native v1 API. It obtains the allowed permission actions from that
fixture's `GET /api/v1/routes` response.

The MCP services are rendered from
`roles/agentgateway_docker/templates/docker-compose.yml.j2` using the existing
role-template test renderer and role defaults. Their image, command, package,
transport, and environment-variable names therefore follow the role source;
the test only adds isolated networks and loopback-only ports, uses test service
names, points `VIKUNJA_URL` at the local fixture, and supplies the generated
synthetic token. The RO and RW positive-control services run the same sidecar
command and package. The positive control uses a second service instance only
so the two tokens remain separate.

The sidecars first join a temporary bootstrap network so the role's native
`npx -y @democratize-technology/vikunja-mcp@...` command can resolve its pinned
package. After both answer `tools/list`, the test disconnects that network.
Vikunja and the sidecars communicate on a unique regular Compose bridge, and
Inspector calls use host ports bound to `127.0.0.1`. The remaining bridge can
still reach external networks; disconnecting the bootstrap network is not an
egress control. The fixture uses only synthetic data and tokens.

The RO token grants only read actions found in the local route catalogue;
the RW positive-control token also grants the current task write actions. This proves
that Vikunja 2.7.1 enforces scoped permissions when the role's MCP package
attempts an update. It does not prove the live deployment's version, its token
contents, or its current authorization behavior. The test must not use a
production URL or production credential.

The test prints the exact MCP error payload from the RO update attempt, with
synthetic token values redacted. It passes only when the RO token can read its
owned task, the update returns MCP `isError: true`, an independent JWT read
shows the title unchanged, and the RW same-tool update succeeds and reads back
the changed title.

## Run locally

Prerequisites are Docker with Compose, a security-patched Node.js release
(22.23.4 or later in the 22.x line), and the pinned Python dependencies in
`.github/requirements-ci.txt`.
The Node runtime runs maintained MCP Inspector `2.12.0`; the Python test reuses
the repository's existing role Jinja renderer through pytest's `pythonpath`
configuration option.

From the repository root:

```sh
python -m pytest -o pythonpath=tests/agentgateway_docker -q -s \
  integration/vikunja-read-token
```

The test creates a unique Compose project and removes its containers and
networks in `finally`. No CI or repository secret is used. The npm
package's direct version is role-pinned; its transitive npm dependency versions
follow that package's published semver ranges, as they do in the role's
`npx` command.
