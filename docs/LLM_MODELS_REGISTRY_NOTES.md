# LLM Model Registry — per-entry notes

Incident history and selection rationale for individual entries in
`llm-models.yml`. Split out of that file for the same reason
[LLM_MODELS_SCHEMA.md](./LLM_MODELS_SCHEMA.md) was: the registry is pure data,
it sits against a per-file token budget, and an agent changing one model's
window should not have to read five unrelated incident write-ups to reach it.

Different jobs, different files. The schema doc says what a **field** means;
this one says why a particular **entry** looks the way it does. Add a note here
and leave a one-line pointer at the entry — never the reverse.

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

## The cluster brain (`mlx-community/GLM-4.7-REAP-50-mxfp4`)

The two-Mac cluster brain (JACCL pipeline across both Macs, ~256 GB combined),
served by mlx-lm rank 0 behind the gate's own cluster TLS site — a DIFFERENT
port on the same host, hence `endpoint: cluster`. Reachable only while a
cluster window is up (cable in); normal serving quiesces during windows, so
this entry is what keeps a brain reachable then.

It carries NO `stable_aliases` in either state: `hermes-default` resolves to the
primary and reaches this entry through `router_settings.fallbacks`, so both
backends answer under one consumer name without a second alias pinning traffic
to a gate that is usually down.

**Flipping `servable`** must happen together with `llm_router_cluster_leg_available`
in `roles/llm_router/defaults/main/50-servable.yml`. `tasks/assert-cluster-leg.yml`
fails the converge if the two disagree — deliberately, since this field is a
manual claim and nothing else catches it drifting from what the role believes
is actually reachable. That exact drift left the entry advertised for a month
after the Thunderbolt cable came out.

## The OpenRouter egress tier

Served under their REAL upstream ids. NEVER part of any fallback chain — a
flaky or rate-limited upstream must not be able to degrade the local brain;
consumers opt in by requesting the id explicitly.

**Key model (deliberate)**: ONE OpenRouter API key PER MODEL, irrespective of
which harness or caller makes the request. `key_field` names the per-model field
in OpenBao — canonically `secrets-external/ai/saas/openrouter`, an
internet-reachable SaaS credential. `context_window` is the model's real serving
window (never null — the compress-death rule applies to every entry).

**Operator disclosure 2026-07-19**: these keys are ACCOUNT-WIDE — the per-model
field names were a naming-level guardrail, not a technical one. Because of that,
**the entry list is the egress allowlist**: an entry with a seeded key is the
only way a model becomes reachable, and the `openrouter/*` passthrough that used
to route around it is gone.

There is no router-enforced SPEND cap, and nothing here should be read as
claiming one — the proxy has no store to track spend in, by design. What the
router does enforce is a rate ceiling per egress deployment. Caller-side policy
(deliberate escalation, `:free` rules) is therefore still doing real work rather
than being a backstop.

The `:free` endpoint is rate-limited, and the vendor logs prompt/session data on
that variant — never send confidential material through it.

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
