# LLM Model Registry — egress/cloud entry notes

Split out of [LLM_MODELS_REGISTRY_NOTES.md](./LLM_MODELS_REGISTRY_NOTES.md)
to keep both files under the repo's per-file token budget
(`.token-limits.yaml`). Covers the OpenRouter egress tier, Hermes cloud
value routing, the Hermes local GPU leg's place in that chain, and the free
OpenRouter preset. Local-serving-tier entries stay in the other file.

## The OpenRouter egress tier

Served under their REAL upstream ids. NEVER part of any fallback chain — a
flaky or rate-limited upstream must not be able to degrade the local brain;
consumers opt in by requesting the id explicitly.

LiteLLM receives one `OPENROUTER_API_KEY` for the OpenRouter API provider. Model
access is not represented by extra credentials: **the entry list is the egress
allowlist**, the unrestricted `openrouter/*` passthrough is absent, and Hermes'
fallback group additionally pins exact model ids plus endpoint price, parameter,
data-collection, and ZDR policy.

The spend-cap mechanism exists and is live-wired — Redis-backed
(`roles/redis`), enforced via `router_settings.provider_budget_config` in
`config.yaml.j2`, guarded by `tasks/assert-budget-backing.yml` (fails the
converge if the shared store and the cap separate). It is currently
**disabled**: `llm_router_openrouter_budget_limit` defaults to `0`. Enabling
it is a config change (set the limit), not an architecture change. The
router does separately enforce a rate ceiling per egress deployment
regardless of the spend cap's state — that ceiling, not the disabled spend
cap, is the current backstop against runaway OpenRouter cost.

OpenRouter models are never chained into a router fallback; a consumer opts
in by naming the id directly. The one `:free`-variant entry
(`llm_router/README.md`'s `nvidia/nemotron-3-ultra-550b-a55b:free`) is
rate-limited, and the vendor logs prompt/session data on that variant —
never send confidential material through it. No router fallback chain
contains a `:free` rung.

## Hermes local-first value routing

`hermes-default` is a LiteLLM `auto_router/complexity_router` deployment. Its
local heuristic sends SIMPLE/MEDIUM requests to the resident routine model and
COMPLEX/REASONING requests to the resident primary; classification performs no
provider call. If local serving fails, the original `hermes-default` request
uses one credential-gated, ordered provider chain. Alibaba and Gemini each
receive the original request through a second local heuristic classifier, so
routine and agentic work do not pay for the same cloud model:

- `hermes-cloud-alibaba`: Qwen 3.5 Flash at $0.10/$0.40 per million
  input/output tokens for SIMPLE/MEDIUM work, and Qwen 3.6 Flash at
  $0.25/$1.50 for COMPLEX/REASONING work, using the International endpoint.
  The first is the lowest verified direct routine price; the second is the
  selected lower-cost tool-capable, long-context agentic tier.
- `hermes-cloud-gemini`: paid Gemini 3.5 Flash-Lite at $0.30/$2.50 per million
  input/output tokens for SIMPLE/MEDIUM work, and paid Gemini 3.7 Flash at
  $0.75/$3.75 through 2026-12-31 for COMPLEX/REASONING work. This is the
  independent stable-provider path; operational prompts use the paid service.
- `hermes-cloud-openrouter`: Kimi K2.6 at $0.60/$3.41 or GLM 5.2 at
  $0.7308/$2.297 per million input/output tokens when verified. This is the
  final gateway-diverse tier; both had multiple hosting endpoints, and LiteLLM
  selects the eligible deployment with the lower configured token price.

The value claim is scoped: cost-based routing compares real token prices only
among models declared equivalent in the final OpenRouter tier, and is not a
quality proxy. Direct providers stay ordered by verified price/capability and
failure-domain independence.

LiteLLM v1.97.0 loads all three provider prefixes and these exact ids with
explicit pricing/metadata even where its bundled catalog lags — that proves
configuration compatibility, not upstream availability; activation still
requires a real health request per id. Every route declares `mode: chat`,
context/output metadata, per-attempt timeout, and zero deployment retries,
and the model-group retry policy pins every cloud alias and physical group to
zero 429 retries — so the global eight-retry policy stays a local-serving
congestion control and cannot delay or multiply paid-provider fallbacks.

Cloud entries render only when their provider credential exists and the
shared budget store is configured. Each physical deployment declares 131,072
input / 8,192 generation token limits, 12 RPM, 500,000 TPM, two concurrent
requests, a 2,400s attempt timeout, and zero retries of any kind. The six
deployment-level monthly ceilings total $10.00 ($3.33 Alibaba, $3.33 Gemini,
$3.34 OpenRouter); the account-wide OpenRouter budget stays separately owned
by `llm_router_openrouter_budget_limit`. Re-verify OpenRouter live prices and
Gemini promotional pricing before activation and before 2027-01-01.

### Why MiniMax is two entries

The live keyless catalog (`https://openrouter.ai/api/v1/models`, read
2026-08-02) carries eight `minimax/*` ids, so "add MiniMax" is a selection, not
a lookup. Since the delegation doctrine tells callers to take the cheapest tier
that can actually do a subtask, one entry would force every MiniMax call to pay
for whichever shape it did not need:

| id | context | price per Mtok (in / out) | role |
| --- | --- | --- | --- |
| `minimax-m2.5` | 204,800 | $0.15 / $0.90 | the cheap default |
| `minimax-m3` | 1,048,576 | $0.30 / $1.20 | the long-context one |

`context_window` is the catalog's real serving window in both cases, not a round
number: an entry advertising more than the backend serves dies mid-stream
instead of compacting, which is why the servable-alias contract test exists.
(DeepSeek's `1000000` rounds that same 1,048,576 window — left alone rather than
widened here.)

Both are PAID — neither has a `:free` variant — so both fall under the role's
per-egress rate ceilings, which are rate limits, not a spend cap.

## The Hermes local GPU leg (`hermes-local-4080`)

Same backend as the `vllm`-tier entry, wired as the local backup rung in the
Hermes fallback chain (after the Mac tiers, before every paid leg). Fail-fast
fields (`num_retries: 0`, short `request_timeout`/`stream_timeout`,
`allowed_fails: 50`) mean "available or busy, don't retry me" — a single GPU
either has a free slot or it does not.

`context_window: 32768` (not the primary's 65536) shares the `*ctx4080` YAML
anchor with the guest's own `vllm`-tier `qwen3.8-27b` entry — the two serve
the same weights and must advertise the same window. `enable_pre_call_checks`
still skips a deployment whose window cannot hold the request, so this leg
self-selects out of requests too large for this card, even though it no
longer trims further below the `vllm`-tier entry's own window the way it once
did.

## The free OpenRouter preset (`hermes-cloud-free` / `best-free`)

`@preset/best-free` is a server-side config edited in OpenRouter's dashboard,
so the model behind it changes with no code change here. Never write a
concrete model id in its place; the preset IS the pointer.

`hermes-cloud-free` (`hermes-cloud` tier) and `best-free` (`openrouter` tier)
name the same preset deliberately: they differ in tier and routing role, not
target. The first is the free rung of the Hermes chain; the second is a plain
handle a caller names directly. Neither can be an alias of the other — an
alias may point only at a first-class large-tier entry — and `best-free` gets
no `stable_aliases` for the same reason, hence the memorable `client_model_id`
instead.

`hermes-cloud-free` sets no `monthly_budget`: nothing to bound.
`assert-budget-backing.yml` only fires on a budget with no store behind it, so
absent is unused, not dishonest.
