# LLM Model Registry — per-entry notes

Incident history and selection rationale for individual entries in
`llm-models.d/`, split out for the same reason
[LLM_MODELS_SCHEMA.md](./LLM_MODELS_SCHEMA.md) was: the registry is pure data
under a per-file token budget, and changing one model's window should not mean
reading five unrelated write-ups first. The schema doc says what a **field**
means; this one says why an **entry** looks the way it does. Add the note here
and leave a one-line pointer at the entry — never the reverse.

## The primary (`mlx-community/Qwen3.8-27B-4bit`)

### Thinking is configured serving-side, not here

The catalog starts this worker with
`--chat-template-args {"reasoning_effort":"medium"}` and the registry does not
restate it. That is deliberate: sampling and serving posture belong to whoever
launches the worker, and a second spelling in the registry is exactly the drift
this file exists to remove.

Leaving it unset is not neutral. The model's own chat template then defaults the
effort to `xhigh`, which **measured 0 answer characters on 3 of 3 runs** —
it thinks until it runs out of budget and emits nothing. `medium` answers and
finishes.

`reasoning_effort` is a prompt string, not a token budget, so responses on this
tier are longer and slower than the 35B's. That is the point of a deliberate
tier rather than a regression, and the router's own request budget already
covers minutes-long answers (`ai_router_request_timeout_seconds` 2400,
`ai_stream_read_timeout_seconds` 1800). Do not shorten either one to make this
tier look faster.

### The `65536` window trails the catalog's `131072` (open, not urgent)

Noted 2026-08-30, unresolved on purpose. The registry advertises
`context_window: 65536` for this entry. nix-ai's catalog entry for the same
physical id declares `contextWindowTokens = 131072`, with its own note that the
model supports a native 262,144 and that production roles deliberately sit at
131,072 so the remaining range stays available for separately managed 200K
feasibility work.

Both numbers are defensible and the gap is **direction-safe**, so nothing
changes yet: this field guards against over-advertising, and under-advertising
only truncates early, costing usable context rather than correctness.

It still conflicts with this file's rule that `context_window` is the catalog's
real serving window, not a round number: `65536` was that figure when written,
and the catalog has since moved without the registry following.

Do not simply raise it. Widening what the router advertises changes live
behavior, and the resident profile — not the entry's declared maximum — is what
the worker actually admits. Before changing it, confirm from the worker's own
command line what window the running process was started with, then set this to
that number. If they now agree at 131,072, this note goes away with the edit.

## The routine tier (`mlx-community/Qwen3.6-35B-A3B-4bit`)

The second warm model, resident beside the primary rather than swapping
against it (nix-darwin `maxResidentWorkers = 2`). It held `serving_role:
primary` and all three consumer aliases until 2026-08-14; both moved to the
27B and this entry stayed servable, because it is: the host serves it,
verified from the worker's own command line at converge. It carries a
`serving_role` rather than none so the registry's real invariant still holds —
servable if and only if the entry names the role it serves in. Its `65536`
window is the same resident profile figure as the primary's.

**It is also the universal judge (2026-08-15).** `goal-judge` moved here from
the 9B for one reason: residency. This model is pinned resident (`ttl=0`) and
cannot be evicted, so a judge call costs no cold load — where the 9B is
swap-class (`ttl=900`) with a measured ~79s cold load, and at the 2-3 goal
cards/hour this fabric drains it evicts between nearly every card, so almost
every judge call paid that tax in full. Residency was always the real fix; it
arrived with `maxResidentWorkers = 2` rather than with a timeout raise.

It stays cross-generation to the worker (Qwen3.6 judging Qwen3.8), which is
what the no-self-preference rule actually requires, and thinking is off here so
a verdict does not pay the primary's deliberation cost. The judge and the
worker are separate resident workers with their own serving slots, so they no
longer serialize against each other either.

## The small tier (`mlx-community/Qwen3.5-9B-MLX-4bit`)

`goal-judge` points here, and it is a first-class entry rather than an alias
target because an alias with no `context_window` falls through the `"*"`
wildcard to a null `max_input_tokens` and compresses its caller to death.
32768 is the small-cache tier figure, matching the swap flags it loads under.

It is the only model that may differ from the primary (user decision
2026-07-28 — concurrency belongs to the largest accurate model, the small tier
stays cross-family to it, which is also what an LLM judge should be). The judge
runs here and NOT on the worker's model: same-model judging is self-preference
bias by construction, and it also made worker and judge contend for one serving
slot.

**Corrected 2026-08.** This entry used to be described as "always up for the
judge". Measured live against the deployed serving host and it is false — the
deployed llama-swap config (nix-ai `modules/mlx/llama-swap-topology.nix`) pins
the warm models resident (ttl=0) and holds THIS model as the evictable
swap-class member (ttl=900), the opposite of what was claimed. Measured
cold-load after eviction: ~79s, which exceeds
`hermes_agent_kanban_goal_judge_timeout_seconds` at its former 60s value — see
the goal-judge comment in `roles/hermes_agent/defaults/main/60-kanban-dispatcher.yml`
for the full incident and the precondition for using this model as the judge
target again.

## OptiQ-4bit (`mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit`)

The agentic-bench-winning brain (`ai_default_model`, `all.yml`). A first-class
entry — NOT passthrough — so the default model carries an explicit
`max_input_tokens`: the wildcard `"*"` entry sets none, and a null there makes
consumers fall back to a near-zero context guess and compress every request to
death.

**`extra_body.repetition_penalty` is the anti-repetition-loop fix.** In long
agentic sessions this brain re-emitted the same tool call dozens of times per
turn; the clean agentic bench and the looping sessions differ only in
sampling. A gentle 1.05 penalty taxes verbatim re-emission while preserving
tool-call JSON validity; `extra_body` carries it through to the model server.
`temperature` is deliberately NOT pinned (0.7 restates the engine fallback);
if 1.05 proves insufficient, next match the clean bench's sampling
(temperature ~1.0, `presence_penalty` 0.0) in that same `extra_body`.

**`standby`**: OptiQ is the agent brain and the only brain that stays resident
beside the shard, so it is the entry that gets a same-id failover sibling when
a standby backend exists. Inert by default — see the standby var block in
`roles/llm_router/defaults/main/` for the dedicated-port topology and the
wired-ceiling safety gate.

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
regardless of the spend cap's state. Caller-side policy (deliberate
escalation, `:free` rules) is therefore still doing real work rather than
being the only backstop.

The `:free` endpoint is rate-limited, and the vendor logs prompt/session data on
that variant — never send confidential material through it.

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

`context_window: 16384` (not the primary's 65536) is deliberate:
`enable_pre_call_checks` skips a deployment whose window cannot hold the
request, so this leg self-selects for short requests and a long one falls
through without wasting an attempt.

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

## The OCR tier (`mlx-community/Unlimited-OCR-bf16`)

The only vision-language entry in the registry, and the only one that is not a
chat brain. It is reached by image content parts rather than by a selector var,
which is why no `llm_router_*_model` selector reads `serving_role: ocr`. The
document-upload path routes every page of an upload through it on this same
router, so a page conversion and a chat turn share one endpoint and one
credential.

**Why it is first-class rather than passthrough.** The `"*"` wildcard already
surfaces the id, so visibility is not the reason. Two things are. It carries the
`Unlimited OCR` alias, which is the name a person picks out of a model list —
the physical repo id is not something to ask anyone to recognise. And it pins
`max_input_tokens` from `context_window` instead of falling through the wildcard
to a null one. That matters more here than for a chat model: a page's image
tokens are large, and a truncated page fails as short-but-valid output rather
than as an error, which is the hardest kind of failure to notice downstream.

`context_window: 32768` is the model's own `max_position_embeddings`, read from
`config.json` on the serving host rather than assumed from the family.

**How `servable: true` was established (2026-08-15).** By a returned completion,
not a `/v1/models` listing. A PDF with known ground truth was rasterized to a
page image and sent to the serving host; the transcription came back containing
every distinctive string in the source. This distinction earns its own paragraph
because the registry's worst historical failure was seven entries that were
enabled, advertised, and listed while the backend answered 404 for all of them —
a listing is not evidence of service.

Repeat requests stayed warm at roughly 2-8s per page with the worker resident
between them, so a multi-page document does not pay a cold load per page. The
entry is swap-class on the serving host with a 600s idle TTL, and is evictable
under memory pressure sooner than that; both are correct for a bursty tier.
