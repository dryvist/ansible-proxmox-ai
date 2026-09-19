# llm_router roles: what lives in git, what lives in the database

Split out of `roles/llm_router/README.md` to keep both files under the
repo's per-file token budget (`.token-limits.yaml`).

Authority is split, and the split follows how often each half changes.

| | Lives in | Owned by | Changes |
| --- | --- | --- | --- |
| **Catalog** — which models exist, their cost, context, credentials | `llm-models.d/` → `config.yaml` | this role, per converge | rarely |
| **Roles** — which model a caller-facing name resolves to, and its fallback order | the router database | the Admin UI | often |

Callers name a role — `lead`, `subagent`, `judge`, `cheap`, `embed`, `ocr` —
never a physical model, so a routing change reaches every client at once with no
client edit and no release. The first seed is declared in
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

**`fast`/`subagent` are a deliberate exception to both rules.** Their chain is
local-first by design (try the 4080 before anything wider, then a free rung,
then one paid rung, `long` last) and does carry a paid rung — the truncation
risk the first rule guards against is instead closed by
`context_window_fallbacks` (`defaults/main/37-fallback-entry-points.yml`),
which escapes a request too large for the role's own advertised window
straight to `long` rather than walking the ordinary chain. See
`defaults/main/55-roles.yml`'s note above `_llm_router_fast_subagent_chain`
for the full reasoning, including what is still unverified.

**`fast-gpu` is a third, narrower role — just the 4080, `fallbacks: []`.**
For a caller that wants to address "just the 4080" directly without naming
its physical model id, and build its own fallback sequence around that one
call (e.g. try `fast-gpu`, then its own local model, then the full `fast`
chain above as a catch-all). Contention fails outright, never redirects —
see `defaults/main/57-subagent-lock.yml` for why a redirect here would
defeat the caller's own sequencing.

## Swap a role

In the Admin UI at `/ui` (sign-in from `UI_USERNAME` / `UI_PASSWORD`): **Models**
→ the role's row → change its target, or **Settings → Fallbacks** → reorder.
Effective immediately; no restart, no converge, no git diff.

The same two edits by hand, against the proxy with the master key:

```bash
curl -X POST "$ROUTER/fallback" \
  -H "Authorization: Bearer $LLM_ROUTER_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model": "subagent", "fallback_models": ["<first>", "<second>"], "fallback_type": "general"}'
```
