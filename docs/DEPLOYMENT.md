# Deploying the Hermes / LLM serving fabric

The Hermes Agent brain wiring is unusually easy to break at the **config**
level: a handful of numbers (declared context window, output cap, the timeout
chain) and one indirection (which router alias the agent's model name resolves
to) must all agree, across two roles that never share a play, or the agent boots
into a death loop. On 2026-07-08 four separate config-level mistakes each reached
production and each took the fabric down:

| Breakage | Root cause | Caught by |
| --- | --- | --- |
| Agent refused to start | `context_length` 60000, below Hermes' hard 64,000 floor | L1 context-floor assert; L2 gateway-down + "below the minimum" in journal |
| Every request compressed to death | brain alias fell through the `*` wildcard → null `max_input_tokens` | L1 router-alias assert; L2 tool-call probe fails |
| Generations killed mid-stream | server guard below generation time / inverted timeout chain | L1 timeout-ordering assert |
| "invalid tool call" loop | wrong tool parser / mis-wired brain | L2 tool-call probe returns no valid `function.name` |
| Cron output loops/repeats | `ai-default` alias ran with `repetition_penalty` off (`extra_body` misses the distinct `model_name`) | Cron-cycle watch |

Every one was **machine-detectable before or immediately after converge**. The
two guardrail layers below make that detection automatic, so an unvalidated
converge can no longer reach production silently. The last row (2026-07-14) is
the exception the others are not: the repetition degeneration produced valid
tool calls, so the Layer-2 probe passed — only watching a full cron cycle
surfaced it (see [HERMES_OPS.md](HERMES_OPS.md) "Repetition guard"). Prefer a
cron-cycle observation over declaring a brain-affecting converge settled.

## The staged deployment path

Always deploy the fabric in this order. Each stage gates the next.

1. **Assert (render-time, Layer 1).** `roles/hermes_agent/tasks/assert.yml` and
   `roles/llm_router/tasks/assert.yml` run *first* in their roles (imported at
   the top of `tasks/main.yml`, under the role's own tag). They fail the play
   before anything is installed if a brain-wiring contract is violated:
   - `hermes_agent_model_context_length >= 64000` (Hermes Agent hard floor).
   - the model consumers request resolves to a first-class router alias with a
     non-null context window (not the `*` wildcard passthrough).
   - the timeout chain is ordered `hermes read < router attempt` (the outermost
     link, the nix-darwin server guard, is out of this repo's reach).

2. **Converge.** Apply the roles normally:

   ```bash
   doppler run -- ansible-playbook \
     -i inventory/hosts.yml playbooks/site.yml --tags hermes_agent
   # and, when the router config changed:
   doppler run -- ansible-playbook \
     -i inventory/hosts.yml playbooks/site.yml --tags llm_router
   ```

   **Converging without object-storage inventory access.** Inventory is
   normally the object-storage-published `tofu_inventory.json`, fetched with an
   OpenBao-authed AppRole. From a workstation lacking that AppRole, pin the
   local gitignored cache instead — `inventory_resolve` resolves
   `TOFU_INVENTORY_PATH` first (tier 1) and skips the object-storage fetch
   entirely:

   ```bash
   TOFU_INVENTORY_PATH="$PWD/inventory/tofu_inventory.json" \
     doppler run -- ansible-playbook \
       -i inventory/hosts.yml playbooks/site.yml --tags llm_router
   ```

   The cache is safe when topology is stable (host set unchanged) — the common
   case for a config-only converge. If a host moved or was added, refresh the
   cache from the tofu-proxmox workspace first, or converge with normal
   object-storage resolution.

3. **Verify gate (post-converge, Layer 2).** `roles/hermes_agent/tasks/verify.yml`
   runs at the end of the role, after handlers flush. It proves the wiring
   actually works, not just that it rendered:
   - the `hermes-gateway` service is `active` and stays up (polled with retries);
   - the gateway journal since the restart contains no fatal config-rejection
     line (`below the minimum`, `does not exist`);
   - one **real** chat completion with a `tools` array round-trips through the
     router at the resolved brain and returns a structurally valid tool call
     with a non-empty `function.name`.

   Any failure fails the play loudly with the rollback recipe below.

4. **Observe one cron cycle.** The seeded cron fleet (Splunk sweeps, the daily
   digest, the nightly wiki job) is where a subtly-degraded brain shows up that a
   single probe cannot. After a converge that changed the brain, watch the home
   channel through at least one digest post (hourly) before considering the
   deploy settled.

## Rollback recipe

If the Layer 2 gate fails — or a cron cycle reveals a degraded brain — roll back
to the previous release tag and re-converge:

```bash
git checkout "$(git describe --tags --abbrev=0)~" && \
  doppler run -- ansible-playbook \
    -i inventory/hosts.yml playbooks/site.yml --tags hermes_agent
```

Release tags are `release-please` semver tags (`vMAJOR.MINOR.PATCH`);
`$(git describe --tags --abbrev=0)~` is the commit immediately before the current
release. Nothing here carries destroy-protection — the fabric is designed to be
cleanly re-convergeable, so rolling the config back and re-running is the primary
recovery, never a snapshot restore.

## Rule: every numeric limit traces to a named constraint

Every one of the 2026-07-08 breakages was a bare number that had drifted away
from the constraint that justified it. So: **every numeric limit in role
defaults must carry a comment tracing it to a named constraint** — the source of
truth that dictates the value, not merely a restatement of the value. A reviewer
must be able to see, from the comment alone, what would have to change in the
world for the number to be wrong.

The `hermes_agent_model_context_length` default is the exemplar of the pattern:
the comment enumerates the exact constraints the number answers to — the Hermes
hard floor (with the outage date), the model's native window, and the
prefill-within-stale-budget arithmetic — and says *"do not change it without
re-checking all three."* Follow that shape for any limit you add:

```yaml
# Constraints this number traces to (do not change it without re-checking each):
# (1) <hard limit some component enforces, with where it is enforced>
# (2) <physical/capacity ceiling>
# (3) <derived relationship it must preserve, e.g. an ordering or a rate>
some_role_some_limit: 12345
```

The fabric-wide timeout chain (`ai_stream_read_timeout_seconds`,
`ai_router_request_timeout_seconds` in `group_vars/all.yml`) is named once for
the same reason: the ordering invariant is a single source of truth that the
Layer 1 assert can check across two roles, instead of two unlinked literals that
can silently invert.

## Staging (planned)

A pre-production staging instance that catches these violations by *executing*
the config — the way Hermes itself rejected the 64k-floor violation at startup —
is tracked in [issue #800][staging]. It requires a new guest (a tofu-proxmox
`deployment.json` change), so it is user-gated.

[staging]: https://github.com/dryvist/ansible-proxmox-apps/issues/800

---

[docs.jacobpevans.com](https://docs.jacobpevans.com)
