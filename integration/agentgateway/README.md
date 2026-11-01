---
type: Integration Guide
title: Agentgateway fixture acceptance
description: Describes the local gateway acceptance suite, its fixture boundary, and what a passing run proves.
timestamp: 2026-10-31T21:49:00Z
---

## Scope

The suite renders the role's configuration and runs it with the gateway image
selected by the role, a local MCP service, and a request observer. It uses
fixture caller keys and backend values only. The workflow checks out
Agentgateway at the pinned commit and builds its maintained testbox with the
upstream `testbox-docker` Make target. The image is tagged locally as
`gateway-falsifier/testbox:0.0.1`; it is not published.

The acceptance gate covers one Vikunja target and one allowed caller trust
class. It checks unauthorized requests and positive initialize, list, call,
and continuation traffic against the selected gateway image. The gateway
terminates MCP sessions, so frontend and backend request counts and session
identifiers need not match. A successful tool result, positive backend traffic,
and complete observer traces establish the connected path. Missing, invalid,
and non-allowlisted caller keys must cause no upstream request-count increase
on either `/agents` or `/vikunja`. These are route checks, not a general health
check.

Header assertions use explicit fixture markers on `X-Agent-Key`,
`Authorization`, `Proxy-Authorization`, and `Cookie`. They prove only that
these marker values are absent from observed requests across the gateway to
MCP backend hop; they are not a general secret scanner. The observer is the
experimental Envoy HTTP tap filter, pinned by the Compose fixture. Missing or
incomplete traces fail the suite. The tap does not observe a sidecar's separate
request to the Vikunja API, so this suite makes no claim about that hop's
credential handling.

The rendered role config currently has no native MCP tool-filter policy, so
the suite does not claim forbidden-tool hiding or direct-call denial. A passing
run means only that the assertions above held for the rendered configuration
and gateway image digest used in that run. It does not prove a deployed
environment, real credentials, live cutover, or behavior for other targets and
trust classes.

The real Vikunja read-token write-denial receipt is a separate client-cutover
prerequisite. An error returned by the transport testbox is not that receipt.

The testbox does not provide a `Last-Event-ID` event store. Stream assertions
cover the response and continuation behavior exercised by the fixture; they
do not claim replay or event-history recovery after reconnect. Multi-tier
session isolation remains deferred until the gateway proves native
principal-bound sessions or separate session namespaces. The suite adds no
session translation behavior.

## Run locally

Prerequisites are Docker with Compose, Python dependencies from
`.github/requirements-ci.txt`, and the Agentgateway source checked out at the
commit in `.github/workflows/gateway-falsifier.yml`. Set
`AGENTGATEWAY_SOURCE_DIR` to that source checkout and `FALSIFIER_RUNTIME_DIR`
to an empty temporary directory.

From the repository root, build the pinned upstream fixture image and run the acceptance tests:

```sh
make -C "$AGENTGATEWAY_SOURCE_DIR/controller" testbox-docker IMAGE_REGISTRY=gateway-falsifier
python -m pytest -o pythonpath=tests/agentgateway_docker -q -s integration/agentgateway
```

CI performs the same build from `.agentgateway` and runs
`python -m pytest -o pythonpath=tests/agentgateway_docker -q -s integration/agentgateway`.
