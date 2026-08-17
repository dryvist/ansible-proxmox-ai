# LLM Model Registry Schema

Field reference for `llm-models.yml` (the fabric's single model-name
source — see that file's own header for why it exists and what derives
from it). Moved out of the yml to keep the data file under the repo's
per-file token budget; this schema is documentation, not data.

A model name written twice is the defect class the registry exists to
remove: on 2026-07-28 every consumer alias in the router pointed at
something the serving host does not serve (four simultaneous live 404s),
precisely because the alias map and the serving host's enabled set had no
shared definition.

The registry itself is pure data on purpose — no Jinja, no host/port/URL
literals, no secrets. That is what lets it parse standalone with any YAML
reader, so CI can generate the published alias contract
(`scripts/generate_servable_aliases.py`) from it without an Ansible run.
Anything topology-shaped (base URLs, ports, bearer env names) stays in
`roles/llm_router/defaults/`, selected symbolically by `tier` and `endpoint`.

```text
Required on every entry:
  client_model_id   What a caller sends. Rendered as the LiteLLM
                    `model_list[].model_name`. This is the ONLY name a
                    consumer may use besides a stable alias.
  upstream_model_id The id the backend itself serves. Written explicitly even
                    where it currently equals client_model_id: the two are
                    independent naming decisions, and collapsing them is how a
                    rename upstream silently becomes a rename for callers.
  provider          LiteLLM provider prefix — `openai` (any OpenAI-compatible
                    backend, which is every local tier) or `openrouter`.
  tier              `large` | `light` | `openrouter`. Selects which backend
                    endpoint the role renders and the deployment shape (light
                    entries become TWO same-name deployments, GPU + CPU).
  enabled           false removes the entry from the rendered config entirely.

Optional:
  litellm_model_name  Explicit override for `litellm_params.model`. Omitted
                      everywhere today because the value is STRUCTURALLY
                      composed as `<provider>/<upstream_model_id>` — it is a
                      derivation, not a third independent name. Set it only
                      when a backend needs a LiteLLM route string that is not
                      that composition.
  context_window      The backend's EFFECTIVE SERVING window — what its
                      KV-cache budget (nix-ai catalog cacheMemoryMb) sustains,
                      NOT the model's native max_position_embeddings. Renders
                      as model_info.max_input_tokens/max_tokens and is the
                      fabric's input enforcement via enable_pre_call_checks;
                      Hermes auto-compacts at 75% of it. LiteLLM has no
                      built-in entry for these backend ids, so an omitted
                      value resolves max_input_tokens to null and every client
                      that reads it falls back to a near-zero context guess
                      and compresses its requests to death (outage
                      2026-07-08). Advertising the NATIVE window is the
                      opposite failure: sessions grow past the serving ceiling
                      and die mid-stream instead of compacting.
  servable            The serving host will actually answer for this id.
                      DISTINCT FROM `enabled`, and conflating them is a real
                      outage: the serving host serves only the models its own
                      config enables, and every other catalogued model is
                      demoted to `disabledModels` and returns HTTP 404 — not a
                      degraded answer, no answer. `enabled` means "the router
                      offers it"; `servable` means "the backend serves it".
                      Alias and fallback targets must be servable. NOTE
                      servable does NOT mean pre-loaded or residency-pinned:
                      the small tier is servable and still evictable
                      (ttl=900), with a measured ~79s cold load.
  serving_role        `primary` | `routine` | `small` | `ocr`. Names the
                      role this entry serves in. Repointing the serving host
                      is a move of this one field: `primary` is what the
                      consumer aliases and the fabric's selector vars follow.
                      `routine` names the second warm model on a host that
                      holds more than one — no selector reads it today, and it
                      exists so the invariant below stays exact. `ocr` is the
                      vision-language document tier, reached by image content
                      parts rather than by a selector var; it is named for the
                      same reason `routine` is, so the invariant holds.
                      INVARIANT: servable if and only if serving_role is set.
                      Both halves are load-bearing — a servable entry with no
                      role is a model nothing can name, and a role with no
                      `servable` is a selector pointing at a 404.
                      tests/hermes_agent/test_goal_mode_contract.py enforces
                      it.
  stable_aliases      Consumer-facing role names for this entry, rendered into
                      `router_settings.model_group_alias`. HARD RULE (AGENTS.md):
                      an alias carries ZERO deployment configuration and never
                      becomes its own model_list entry — duplicating a physical
                      entry's context_window/extra_body/api_base under a second
                      name silently drifts from the real backend every time the
                      model changes (root cause of the #1004 diagnosis cost).
  endpoint            Symbolic backend selector WITHIN a tier. Only `cluster`
                      is defined today (the two-Mac cluster gate's own TLS
                      site); omitted means the tier's normal endpoint. The
                      actual URL/port lives in the role, never here.
  extra_body          Sampling parameters forwarded to the backend verbatim.
  embedding           light tier only — marks the entry as an embeddings group.
  standby             large tier only — also render a same-id, same-window
                      failover deployment when the role has a standby backend
                      URL configured.
  key_field           openrouter tier only — the per-model field in OpenBao
                      secrets-external/ai/saas/openrouter. An entry whose key
                      is not seeded renders nothing, so the list is safe to
                      extend ahead of key seeding.
```
