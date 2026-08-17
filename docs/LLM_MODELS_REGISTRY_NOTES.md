# LLM Model Registry — per-entry notes

Incident history and selection rationale for individual entries in
`llm-models.yml`. Split out of that file for the same reason
[LLM_MODELS_SCHEMA.md](./LLM_MODELS_SCHEMA.md) was: the registry is pure data,
it sits against a per-file token budget, and an agent changing one model's
window should not have to read five unrelated incident write-ups to reach it.

Different jobs, different files. The schema doc says what a **field** means;
this one says why a particular **entry** looks the way it does. Add a note here
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
agentic sessions this brain emitted ~37 IDENTICAL tool calls per turn (the
agent dedups and executes once, the model re-emits next turn, the task never
advances). The 20/20-clean agentic bench and the looping production sessions
differ only in sampling. A gentle 1.05 repetition penalty directly taxes
verbatim re-emission while staying mild enough to preserve tool-call JSON
validity. `extra_body` carries it through the router to the MLX model server.

`temperature` is deliberately NOT pinned: 0.7 would merely restate the engine
fallback the loop already runs under. If 1.05 proves insufficient, the next
lever is to match the clean bench's sampling — temperature ~1.0 /
`presence_penalty` 0.0 — added to that same `extra_body`.

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

There is no router-enforced SPEND cap, and nothing here should be read as
claiming one — the proxy has no store to track spend in, by design. What the
router does enforce is a rate ceiling per egress deployment. Caller-side policy
(deliberate escalation, `:free` rules) is therefore still doing real work rather
than being a backstop.

The `:free` endpoint is rate-limited, and the vendor logs prompt/session data on
that variant — never send confidential material through it.

## Hermes local-first value routing

`hermes-default` is a LiteLLM `auto_router/complexity_router` deployment. Its
local heuristic sends SIMPLE/MEDIUM requests to the resident routine model and
COMPLEX/REASONING requests to the resident primary; classification performs no
provider call. If local serving fails, the original `hermes-default` request
uses one credential-gated, ordered provider chain:

- `hermes-cloud-alibaba`: Qwen 3.6 Flash, International, at a verified
  $0.25/$1.50 per million input/output tokens up to 256K input. This is the
  lowest verified direct price for the selected tool-capable, long-context
  agentic tier.
- `hermes-cloud-gemini`: paid Gemini 3.7 Flash at a verified $0.75/$3.75 per
  million input/output tokens through 2026-12-31. This is the independent
  stable-provider option for multi-step agentic work; operational prompts use
  the paid service.
- `hermes-cloud-openrouter`: Kimi K2.6 at $0.60/$3.41 or GLM 5.2 at
  $0.7308/$2.297 per million input/output tokens when verified. This is the
  final gateway-diverse tier; both had multiple hosting endpoints, and LiteLLM
  selects the eligible deployment with the lower configured token price.

The value claim is scoped: cost-based routing compares real token prices only
among models declared equivalent in the final OpenRouter tier. It does not
pretend raw price measures quality. Direct providers remain deliberately ordered
by verified price/capability and failure-domain independence.

Cloud entries render only when their single provider credential exists and the
shared budget store is configured. Each deployment is capped at 131,072 input
tokens, 8,192 output tokens, 12 RPM, 500,000 TPM, and two concurrent requests.
Ordinary retries remain zero. The four deployment-level monthly ceilings total
$10.00. The existing account-wide OpenRouter budget remains separately owned
by `llm_router_openrouter_budget_limit`; no additional daily-provider ceiling
is implied here. Re-verify OpenRouter live prices and Gemini promotional
pricing before activation and before 2027-01-01.

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
number: the servable-alias contract test exists because an inflated window is
the compress-death failure, and an entry advertising more than the backend
serves dies mid-stream instead of compacting. (DeepSeek's `1000000` is a
pre-existing rounding of the same 1,048,576 window — left alone rather than
widened in a change about something else.)

Both are PAID — neither has a `:free` variant — so both fall under the rate
ceilings the role applies to every egress deployment. Those are rate limits, not
a spend cap.
