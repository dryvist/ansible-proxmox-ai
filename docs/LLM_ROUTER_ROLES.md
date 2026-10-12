# llm_router roles: what lives in git, what lives in the database

Split out of `roles/llm_router/README.md` to keep both files under the
repo's per-file token budget (`.token-limits.yaml`).

Authority is split, and the split follows how often each half changes.

| | Lives in | Owned by | Changes |
| --- | --- | --- | --- |
| **Catalog** — which models exist, their cost, context, credentials | `llm-models.d/` → `config.yaml` | this role, per converge | rarely |
| **Roles** — which model a caller-facing name resolves to, and its fallback order | the router database | the Admin UI | often |

Callers name a capability — `small`, `medium`, `large`, `embed`, `ocr` — or a
seeded role such as `judge` or `recorder`, never a physical model, so a routing
change reaches every client at once with no client edit and no release. The first seed is declared in
`defaults/main/55-roles.yml`; `tasks/seed-roles.yml` writes a role only when the
running proxy carries no deployment by that name, so **a converge never
overwrites an edit made in the UI**. Change a role in the UI, not in git —
editing the seed affects only a proxy that has never carried the role before.

Roles require the database (`llm_router_db_host`) and
`llm_router_store_model_in_db`; with neither, config-rendered aliases still work
and no role is seeded.

Two rules bind a seeded fallback rung, and a rung must satisfy both:

- **Largest context first.** A shorter-context fallback does not degrade a
  caller, it truncates one, and `enable_pre_call_checks` rejects the over-long
  request rather than routing it — so a short rung behind a long-context model
  is a hard failure exactly when it was meant to help.
- **Zero cost.** A role carrying delegated bulk work must not fall into
  metered egress: an outage would become spend nobody chose. Paid models stay
  reachable by name; they are never something a role falls into.

Where no catalogued model satisfies both, the role is seeded with no fallback
record at all. It then fails honestly rather than silently, and whoever owns
the UI adds a rung deliberately.

**`fast`, `cheap` and `subagent` are aliases, not roles.** They resolve through
`llm_router_model_group_aliases` to the `small` or `medium` capability, whose
members run in deployment order (local GPU first, then the local fallbacks, then
the capability's own cloud tail). A request too large for the advertised window
escapes through `context_window_fallbacks`
(`defaults/main/37-fallback-entry-points.yml`) rather than walking the ordinary
chain. Only `judge` and `recorder` are seeded as database roles
(`defaults/main/55-roles.yml`), and they copy the first member of their
capability.

## Swap a role

In the Admin UI at `/ui` (Authelia SSO sign-in): **Models**
→ the role's row → change its target, or **Settings → Fallbacks** → reorder.
Effective immediately; no restart, no converge, no git diff.

The same two edits by hand, against the proxy with the master key:

```bash
curl -X POST "$ROUTER/fallback" \
  -H "Authorization: Bearer $LLM_ROUTER_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model": "subagent", "fallback_models": ["<first>", "<second>"], "fallback_type": "general"}'
```
