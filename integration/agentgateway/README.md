---
type: Integration Guide
title: Agentgateway fixture acceptance
description: Describes the local gateway acceptance suite, its fixture boundary, and what a passing run proves.
timestamp: 2026-10-31T21:49:00Z
---

## Scope

The suite renders the role's configuration and runs the pinned gateway with
the role-rendered Vikunja MCP sidecar, a local Vikunja API, the maintained
Agentgateway testbox, and one Envoy observer. It uses synthetic caller keys
and backend tokens only. The workflow checks out Agentgateway at the pinned
commit and builds its maintained testbox with the upstream `testbox-docker`
Make target. The image is tagged locally as `gateway-falsifier/testbox:0.0.1`;
it is not published.

The acceptance gate covers the strict `/agents` facade and the existing
optional API-key `/vikunja` route. It checks strict denial and positive
initialize, list, call, continuation, SSE, and tool-error traffic on
`/agents`, with denied callers producing no testbox traffic. The `/vikunja`
test makes an actual task read without `X-Agent-Key`
through the gateway, the role-rendered sidecar, and the pinned local Vikunja
API. A separate render check compares the complete `/vikunja` route block
byte-for-byte with the facade flag enabled and disabled.

The same Envoy observer has three listeners with separate clusters, counters,
and trace directories: `8002` observes gateway-to-testbox MCP traffic, `8003`
observes gateway-to-sidecar MCP traffic, and `3456` observes the sidecar's
Vikunja API requests. The sidecar's API URL points through the `3456` observer
listener to the Vikunja fixture; the observer forwards to the API service on a
separate network. Listener controls prove both new taps see their intended
positive traffic, then clear their traces and counters before the acceptance
requests.

The `8003` traces must contain no caller credential headers. The `3456` traces
must carry exactly the sidecar's generated backend `Authorization` value and
must contain no caller credential headers or caller marker values. The suite
checks completed-request counts against complete tap files and waits for active
requests to drain. These marker checks cover the fixture values and headers;
they are not a general secret scanner.

The rendered role config currently has no native MCP tool-filter policy, so
the suite does not claim forbidden-tool hiding or direct-call denial. A passing
run means only that the assertions above held for the rendered configuration
and gateway image digest used in that run. It does not prove a deployed
environment, real credentials, live cutover, or behavior for other targets and
trust classes.

The direct Vikunja RO-denial and RW-positive-control test remains a separate
client-cutover prerequisite. An error returned by the transport testbox is not
that receipt.

The testbox does not provide a `Last-Event-ID` event store. Stream assertions
cover the response and continuation behavior exercised by the fixture; they
do not claim replay or event-history recovery after reconnect. Multi-tier
session isolation remains deferred until the gateway proves native
principal-bound sessions or separate session namespaces. The suite adds no
session translation behavior.

## Run locally

Prerequisites are a local Unix-socket Docker engine with Compose, Python
dependencies from `.github/requirements-ci.txt`, and the Agentgateway source
checked out at the commit in `.github/workflows/gateway-falsifier.yml`. Set
`AGENTGATEWAY_SOURCE_DIR` to that source checkout and `FALSIFIER_RUNTIME_DIR`
to an empty temporary directory.

From the repository root, build the pinned upstream fixture image and run the acceptance tests:

```sh
make -C "$AGENTGATEWAY_SOURCE_DIR/controller" testbox-docker IMAGE_REGISTRY=gateway-falsifier
python -m pytest -o pythonpath=tests/agentgateway_docker -q -s integration/agentgateway
```

CI performs the same build from `.agentgateway` and runs
`python -m pytest -o pythonpath=tests/agentgateway_docker -q -s integration/agentgateway`.
