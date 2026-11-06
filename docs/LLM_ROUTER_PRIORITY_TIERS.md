---
type: reference
description: Priority teams, deployment tags, and timeout precedence for router consumers.
tags: [llm-router, routing]
timestamp: 2026-11-06T23:20:00Z
---

# Priority tiers

Local model groups render 3 deployment entries per backend, tagged `tier-0`,
`tier-1`, and `tier-2`. Their `extra_body.priority` values are 1, 10, and 20.
Each entry retains the backend's configured admission cap and order. The catalog
projects `execution_width` from the serving profile and `admitted_waiters` from
the difference between admission and execution width. `max_parallel_requests`
is a per-deployment, per-worker limit; tier copies do not establish a shared
physical-lane limit or reserve capacity for a tier. Tier 1
also carries `default` for requests without a tier tag.

Consumer keys belong to the `tier-0`, `tier-1`, or `tier-2` team. The input map
`llm_router_priority_key_tiers` assigns consumer aliases; an entry's
`priority_tier` overrides that map. Unmapped keys use `llm_router_priority_default_tier`.
Key and team metadata carry required tier tags. Tier 2 also requires `local`,
disables key fallback maps, and receives deployment `timeout` and `stream_timeout`
of 45600 seconds with `keepalive_seconds: 15`. This combines 43200 seconds of
waiting and the existing 2400-second attempt budget. Tier-2 key and team settings
carry the same attempt timeout. Native background clients use 45000 seconds,
including their existing 1800-second execution budget; the workflow runtime uses
the attempt budget. Tier-0 and tier-1 client and attempt defaults stay unchanged.
Cloud deployments carry only
tiers 0 and 1 and retain their privacy tags.
Hermes agent endpoints carry all priority tier tags and retain `sensitivity:public`.
Keys moving from tier 2 to tier 0 or 1 receive the new tier tag and fallback
policy. The `priority_policy` metadata records injected settings and their prior
values. Reconciliation restores unchanged tier-owned values and retains explicit
seed restrictions, pre-existing operator policy, and subsequent operator edits.
Rows without provenance retain their existing restrictions until ownership is
recorded. DB role creation, rebuild, and live updates use one tier projection;
the role's consumer key determines priority and patience. Stored targets and
custom parameters survive policy updates. Tier-2 roles retargeted to cloud carry
no tier-2 deployment grant.

The LiteLLM release pinned in
[`files/requirements.in`](../roles/llm_router/files/requirements.in), checked 2026-11-06,
supports deployment keepalives in
[`proxy_server.py`](https://github.com/BerriAI/litellm/blob/ea7ca16dbc3904d9e59a9505f379a2ca3091821f/litellm/proxy/proxy_server.py)
and resolves deployment timeouts before router defaults in
[`router.py`](https://github.com/BerriAI/litellm/blob/ea7ca16dbc3904d9e59a9505f379a2ca3091821f/litellm/router.py).
Explicit request timeouts take precedence over deployment timeouts.
Key/team `timeout` overrides are forwarded by
[`route_llm_request.py`](https://github.com/BerriAI/litellm/blob/v1.106.4/litellm/proxy/route_llm_request.py).
These settings define timeout budgets; queue ordering and a full-duration run
require separate runtime acceptance.

See the [role reference](../roles/llm_router/README.md) for configuration and
[backend tiers](LLM_ROUTER_TIERS.md) for the model-group map.
