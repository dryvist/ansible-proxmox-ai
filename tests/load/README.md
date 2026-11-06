---
type: procedure
description: Run concurrent k6 chat scenarios and reconcile every tier 2 request with router spend logs.
tags: [llm-router, acceptance, load-testing]
timestamp: 2026-11-06T22:35:00Z
---

# Tier 2 burst and tier 0 latency acceptance

Run this test from the supervising operator's shell after the priority-tier
configuration is deployed. It sends chat requests to `/v1/chat/completions`.
The test changes no role configuration. Live acceptance remains a separate run.

## 1. Requirements

- [k6 in nixpkgs](https://github.com/NixOS/nixpkgs/blob/nixos-unstable/pkgs/by-name/k6/k6/package.nix)
  is available through `nix run nixpkgs#k6` (checked 2026-11-06).
- k6 automatically resolves the maintained community
  [SSE extension](https://github.com/phymbert/xk6-sse), imported as `k6/x/sse`.
  [Automatic extension resolution](https://grafana.com/docs/k6/latest/extensions/run/)
  requires public download access before execution. Record the resolved k6 and
  extension versions with the run artifacts.
- Use the priority-team and deployment-tag contract from
  [the priority tiers change](https://github.com/dryvist/ansible-proxmox-ai/pull/2055).
  Supply separate tier 2 and tier 0 keys with required tier tags and local-only
  model access. Both scenarios use private synthetic traffic and `&local`.
  Allow at least 1000 pending tier 2 requests and 10 tier 0 requests at the key
  and team layers. Check the controller's file-descriptor limit and memory
  before allocating 1010 virtual users.
- Every network hop must permit a 12-hour request. Keep model aliases and
  deployment policy in the router's registry. Select aliases that reach all
  eligible local lanes; use the currently approved model dossiers.

Supply these variables through the shell's approved environment injection.
Keep keys and endpoint values outside this repository and command arguments.

| Variable | Input |
| --- | --- |
| `ROUTER_BASE_URL` | HTTPS origin, without a path, query, or embedded credentials |
| `ROUTER_TIER2_KEY` | Dedicated tier 2 virtual key |
| `ROUTER_TIER0_KEY` | Dedicated tier 0 virtual key |
| `ROUTER_TIER2_MODEL` | Approved local model-group alias for tier 2 |
| `ROUTER_TIER0_MODEL` | Approved local model-group alias for tier 0 |
| `LOAD_RUN_ID` | Unique run label: 1-64 letters, digits, underscores, or hyphens |
| `LOAD_ARTIFACT_DIR` | New private directory outside the checkout |

Requests use documented [required routing tags](https://docs.litellm.ai/docs/proxy/tag_routing)
in `metadata.tags`: `&tier-2` or `&tier-0`, plus `&local`.
The key's inherited policy enforces the same scope. `agent: acceptance-load`,
numeric `tier`, and `privacy_class: private` describe the synthetic workload.
`metadata.request_id` is unique per scenario iteration; the same ID, run label,
and tier go into LiteLLM's documented
[`metadata.spend_logs_metadata`](https://docs.litellm.ai/docs/proxy/cost_tracking#custom-spend-log-metadata).
`metadata.session_id` groups the run. These accounting fields do not assign a team.
The unique synthetic message and `cache.no-cache` request option exercise inference.

## 2. Local validation

These commands contact no router:

```sh
nix run nixpkgs#k6 -- inspect tests/load/tier2-burst.js
pytest tests/load/ -q
pre-commit run --files tests/load/tier2-burst.js tests/load/README.md tests/load/test_tier2_burst.py
```

The pytest checks run the actual k6 module with Node's native module mocks.
They cover request accounting, response errors, streaming framing, and setup
validation without a network listener or live credentials.

## 3. Operator execution

Start existing lane/queue monitoring before the test. Record busy slots and
waiting requests for every eligible lane, with UTC timestamps. Confirm the
lanes are saturated during the tier 0 measurements, then preserve that evidence.
An idle or cache-served run does not prove latency under saturation.

```sh
: "${LOAD_ARTIFACT_DIR:?Set a new private artifact directory outside the checkout}"
umask 077
mkdir "$LOAD_ARTIFACT_DIR"
nix run nixpkgs#k6 -- version > "$LOAD_ARTIFACT_DIR/k6-version.txt"
nix run nixpkgs#k6 -- run --no-usage-report --log-format raw \
  --console-output "$LOAD_ARTIFACT_DIR/requests.jsonl" \
  --out "json=$LOAD_ARTIFACT_DIR/metrics.json" \
  --summary-export "$LOAD_ARTIFACT_DIR/summary.json" \
  tests/load/tier2-burst.js
```

- **Scenario A:** 1000 arrivals spread across 60 seconds. All 1000 virtual
  users are preallocated so queued requests keep their original IDs.
  Each request permits 12 hours, with a 12-hour-and-1-minute graceful drain
  after arrivals stop. There are no client retries. A 429 is an error.
- **Scenario B:** 1 streaming request every 5 seconds from second 10 through
  second 60, yielding 10 latency samples alongside the burst. Each gets the
  same timeout and drain allowance. Keepalive comments and role-only frames
  do not count as tokens. The first content or reasoning token determines
  `tier0_ttft_ms`; `med` is p50, and `p(95)` is p95. The measurement includes
  connection setup and queue wait. Incomplete or errored streams fail.
- **Records:** `tier2_result` records contain the unique ID, HTTP status,
  total latency, error code, completion flag, timeout flag, and UTC end time.
  `start` and `end` records bound the window. Raw metric points retain IDs and
  statuses. Preserve the exit status, summary, records, and monitoring evidence
  privately; runtime artifacts can contain operational identifiers.

## 4. Acceptance and spend accounting

Accept only if k6 exits 0, all 1000 tier 2 requests complete, errors and
timeouts equal 0, no iteration is dropped, and all 10 tier 0 streams complete.
The first-token trend must have samples. There is no invented latency target:
report measured p50 and p95 with the overlapping saturation evidence.

Check the client ledger:

```sh
jq -s -e '
  [.[] | select(.event == "tier2_result")] as $rows |
  ($rows | length) == 1000 and
  ($rows | map(.request_id) | unique | length) == 1000 and
  all($rows[]; .completed and (.timeout | not) and .status == 200)
' "$LOAD_ARTIFACT_DIR/requests.jsonl"
```

Use the router's own spend-log API with an approved read identity. Endpoint
paths below are relative to the injected router origin. Keep authorization in
the API client's environment. Inspect `/docs` for the deployed schema.

1. Use the `start`/`end` UTC timestamps, rounded outward to seconds, formatted
   `YYYY-MM-DD HH:MM:SS` and URL-encoded. Retrieve:

   ```text
   GET /spend/logs/v2?start_date=<start>&end_date=<end>&session_id=<LOAD_RUN_ID>&page=1&page_size=1000
   ```

   Fetch every page through `total_pages`, without a success-only filter.
   Wait for spend-log flush and repeat until stable. Save the response pages
   privately as `spend-page-*.json`. Retain every attempt, including failures
   and fallbacks; a zero-cost total alone is insufficient.
2. Retrieve `/model/info` and resolve each returned `model_id` against the
   operator-approved local deployment IDs. Verify deployment locality with
   the reviewed router configuration; an OpenAI-compatible provider name alone
   cannot distinguish local serving from cloud. Preserve that mapping privately.
3. Select tier 2 rows by `metadata.spend_logs_metadata.tier == 2` and the exact
   run label, then group by `metadata.spend_logs_metadata.request_id`. Compare
   the resulting ID set with the 1000 IDs in the client ledger. Require every
   ID exactly once as a successful final completion, no missing/extra IDs, and
   **zero cloud deployment attempts** across all rows for those IDs. Treat
   unknown deployment IDs, absent metadata, truncated pagination, cache hits,
   and missing rows as unverified acceptance. If needed, retrieve individual
   rows through `/spend/logs/v2?request_id=<router-request-id>&page=1&page_size=1000`.

The [LiteLLM spend API implementation](https://github.com/BerriAI/litellm/blob/v1.106.4/litellm/proxy/spend_tracking/spend_management_endpoints.py)
defines the filters, UTC format, and pagination above (checked 2026-11-06).
Record client counts, saturation window, tier 0 p50/p95, complete spend-log
coverage, and the zero-cloud count in the supervising acceptance task.
