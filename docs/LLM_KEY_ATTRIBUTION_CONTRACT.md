# LLM request attribution contract

One documented shape every client, dashboard and cost query uses to answer
"who spent this" — instead of each dashboard guessing at `key_alias` meaning
different things in different places. Companion to
`LLM_ROUTER_OBSERVABILITY.md` (the serving-share/fallback measurement); this
document is about attribution, not serving health, and does not repeat that
one's content.

## The two layers

1. **Key-level metadata** (`roles/llm_router/tasks/seed-keys.yml`, set on
   every `/key/generate` and reconciled onto every already-minted key). Two
   fields, both derived, never hand-listed:
   - `app` — the key's own alias (`56-virtual-keys.yml`). Never re-typed:
     the seed task reads `item.alias`, so a renamed key stays in sync with
     zero doc/dashboard edit.
   - `tier` — `subscription` for a key carrying a daily `daily_budget` (a
     metered consumer against the shared subscription/paid ceiling), `local`
     otherwise (a budget-less key draws only on the free/local-only serving
     tier this router never meters). An entry may override either field with
     its own `metadata`/`tier` key when the derived value is wrong for it.
2. **Request-level tags** — a client sets these on the individual completion
   call (`extra_body.metadata` or the `X-Litellm-Tags` header), not on the
   key. This is where `device`, `host`, `agent`, `session` and `zdr` belong:
   they vary per call, not per credential, so baking them into the key would
   mean minting a new key per session, which the router has no lifecycle for.
   The same request metadata accepts `runner`, `purpose`, and `tier` for
   benchmark and live request attribution.
   - `device` / `host` — the calling machine's short hostname.
   - `agent` — `claude` \| `codex` \| `opencode` \| `hermes` \| `donna` \|
     `openwebui` \| ... — which coding/chat surface issued the call, distinct
     from `app` (the router-key consumer) when one key legitimately serves
     more than one agent binary (e.g. `litellm-local`, a single workstation
     proxy in front of whichever CLI is running).
   - `session` — the CLI's own session/conversation id, so a dashboard can
     group by run without a database join back to a transcript.
   - `zdr` — `true` only for a call that must never reach a non-ZDR model
     (mirrors the `zero_data_retention` enforcement already live for the
     `hindsight` key's `free`-only scope — see `56-virtual-keys.yml`).

A row missing the request-level tags still rolls up correctly under `app`/
`tier` from the key; the two layers are additive, not a fallback chain a
dashboard has to special-case.

## Enumerated virtual keys (`56-virtual-keys.yml`, as of this pass)

Every alias below is a distinct key today — no two consumers share one
value — with one caveat noted where checked:

| Alias | Scope | Tier | Notes |
| --- | --- | --- | --- |
| `open-webui` | full | subscription | admin-managed chat UI |
| `langfuse` | full | subscription | Langfuse's own playground/eval connection |
| `langgraph` | cheap, embed | local | graph workers, classification only |
| `n8n` | cheap | local | automation node |
| `agentgateway` | embed | local | docs-search MCP tool |
| `dify` | full | subscription | admin-managed UI |
| `llamaindex` | embed | local | RAG indexer |
| `hindsight` | free only | local | `zdr: true` by construction — enforced structurally, not tagged |
| `prometheus-scrape` | none (metrics only) | n/a | scrape auth, not a chat consumer |
| `zammad` | cheap | local | AI provider integration |
| `github-actions` | review-private, review-local | local | CI, private repos |
| `github-actions-oss` | review-oss | local | CI, public repos |
| `opencode` \| `raycast` \| `codex` \| `cursor` \| `claude-code` | full | subscription | one key per **app**, shared across every host that runs it |
| `litellm-local` | fast, fast-gpu | local | one workstation's local proxy |
| `hermes-<profile>` | per-profile | subscription (`default`) / local (named) | one key per Hermes operating profile |

**Confirmed shared-across-device today:** `codex`, `cursor`, `raycast`,
`claude-code`, `opencode` — each is one router key regardless of which
machine's CLI presents it, so the router's own spend log cannot separate
"the MBP's Claude Code" from "the Studio's Claude Code" without the
request-level `device`/`host` tag above. This is exactly the gap the
request-level layer closes: splitting these into per-host keys would trade
one dashboard-side join for N times the key/budget/rotation bookkeeping, for
attribution the header already gives for free. **Recommendation: tag, don't
split**, unless a host-level budget ceiling (not just a dashboard filter)
is later required — that's the one case that needs a second key, tracked
separately if it comes up.

**Confirmed NOT shared today:** `litellm-local` — only `macbook-m4/home.nix`
declares `programs.litellmLocal.enable`; `mac-studio/home.nix` has no router
reference in `home.nix` at all (unconfirmed whether Mac Studio reaches the
router through some other path not checked this pass).

Claude Code on `macbook-m4` currently talks to Anthropic **directly**
(`litellmLocal.claudeDirect = true`, `nix-darwin/hosts/macbook-m4/home.nix`)
— it does not traverse this router or carry the `claude-code` virtual key at
all on that host. Whatever consumer currently presents the `claude-code` key
is unconfirmed this pass; do not assume it is this machine's Claude Code.

## Where dashboards read this

Every panel groups by `metadata.app` primarily (present on every request,
including one missing every request-level tag) and by the request-level
`agent`/`device`/`tier` when present, never by raw `key_alias` — the alias is
an operational rotation handle, not a stable spend dimension.
