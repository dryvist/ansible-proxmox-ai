# redis

Shared Redis instance backing the LiteLLM router pool's cross-instance spend
accounting.

## Why this is its own guest

Every other Redis in this estate is colocated with the single workload that
uses it — Dify bundles one, Nautobot and Zammad run one on loopback, Immich
bundles valkey. That is the right shape for a single-instance consumer and the
wrong one here.

`llm_router_group` is a multi-member pool, converged `serial: 1`. A colocated
instance would give each member its own private spend counter, which is exactly
the miscount that made an earlier unbacked budget dishonest: the rendered
config advertised a ceiling that was really N times its stated value, and every
rolling converge reset it. A shared ceiling needs a shared store, and a shared
store cannot live inside one of the things sharing it.

## Scope

Deliberately **not** a general-purpose estate cache. One workload, one instance
— the same split object storage uses here. A second consumer gets its own
instance rather than a second logical DB in this one, so a runaway neighbour
cannot evict spend counters.

## What the configuration choices are protecting

`maxmemory-policy noeviction`
: These keys *are* the spend ceiling. Evicting one under memory pressure
  silently **raises** the effective budget instead of enforcing it — an
  invisible failure that points the wrong way. Failing writes is the correct
  behaviour for a store whose whole purpose is to say no.

`appendonly yes`
: A restart that came back empty would zero the period's accumulated spend.
  That is one of the two specific failures that made the earlier budget
  dishonest, the other being per-member counting.

`requirepass`, mandatory with no default
: An unauthenticated Redis reachable off-loopback is a remote-code-execution
  primitive (`CONFIG SET dir` followed by `SAVE`). There is no password-less
  branch: an absent secret fails the converge rather than quietly standing up
  an open instance.

`rename-command CONFIG/MODULE/DEBUG ""`
: Removes the commands that turn a reachable Redis into that primitive.
  LiteLLM needs none of them.

Binds the guest address, not loopback
: Loopback *is* the colocated pattern this role exists to avoid. The Proxmox
  firewall restricts who may dial it, as it does for every other service here.

## Availability note

LiteLLM reads through a DualCache — in-memory first, Redis on miss — so a Redis
outage degrades the ceiling back to per-member counting rather than failing
closed. That is the right trade for the fabric's single front door: a store
outage must not take serving down with it. But it means Redis is a
**correctness** dependency, not merely an optimisation, and "a budget is
configured" is not the same claim as "spend is capped right now".

## Requirements

- `LLM_ROUTER_REDIS_PASSWORD` from OpenBao (or the env fallback).
- `tofu_data.constants.service_ports.redis_default` for the port; no literal.
- A guest to run on, and a firewall rule permitting the router pool to reach it.
