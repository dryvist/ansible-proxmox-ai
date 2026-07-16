# Escalation rubric — feature schema (deep 353B vs broad 30B tier routing)

Schema for the task features that predict when the two-Mac 353B RDMA cluster
(deep-reasoning tier, single-stream, cluster-window-scheduled) beats the 30B
(broad tier, batched/concurrent). The actual rubric run populates the measured
values; this file defines WHAT to measure and HOW.

Tiers:

- **deep** — 353B cluster: one deep stream, high per-token cost, high latency,
  strongest reasoning. Scarce: quiesces the normal serving fabric while up.
- **broad** — 30B (OptiQ-35B/Coder-30B class) via the LiteLLM router: cheap,
  concurrent, always-on; the current Hermes brain.
- **either** — no predicted quality gap worth the deep tier's cost.

## Features

Each feature: definition, how to measure it, scale 0-3. Measure twice where
possible — **prospective** (estimated from the task prompt before running) and
**retrospective** (observed from the run transcript/events). The rubric is
trained on the retrospective values vs the per-tier outcome grades; the
router uses the prospective estimates.

### F1. reasoning_depth

Longest chain of dependent inferences required — steps where step N's input is
step N-1's conclusion (not just its data).

- Measure (prospective): count dependent stages in the task's success criteria.
  (retrospective): longest causal chain in the transcript from raw evidence to
  a delivered conclusion.
- 0 = lookup/report; 1 = 2-3 dependent steps; 2 = 4-6; 3 = ≥7 or requires
  revising an earlier conclusion mid-chain.

### F2. context_breadth

Volume + heterogeneity of context that must be held simultaneously for the
final synthesis (not merely touched sequentially).

- Measure (prospective): count distinct sources (indexes, repos, config files,
  memory areas) the deliverable must JOIN. (retrospective): peak context
  tokens at synthesis + number of distinct sources cited in one output section.
- 0 = single source; 1 = 2-3 homogeneous; 2 = 4-6 or 2+ heterogeneous kinds;
  3 = ≥7 sources or ≥3 kinds joined in one artifact.

### F3. tool_chain_length

Length of the longest SEQUENTIAL tool-call chain where each call's arguments
depend on the previous call's result (parallel/independent calls don't count).

- Measure (prospective): estimate from scope steps. (retrospective): walk the
  event stream; count the longest dependent chain, and total tool calls as a
  secondary number.
- 0 = ≤2; 1 = 3-5; 2 = 6-10; 3 = >10 dependent calls.

### F4. ambiguity

How underspecified the task is — how much the agent must decide WHAT the task
even is before doing it.

- Measure (prospective): count of unbound choices the prompt delegates
  (pick-your-own target, self-directed selection, undefined ranking criteria).
  (retrospective): count of explicit interpretation/selection decisions the
  agent had to state.
- 0 = fully specified checklist; 1 = one bounded choice; 2 = several choices
  or one open-ended selection; 3 = the core objective itself must be derived.

### F5. cross_source_synthesis

Whether the VALUE of the output comes from correlating independent sources
(distinct from F2's volume: two sources that must be reconciled score higher
than five that are merely concatenated).

- Measure: count claims in the deliverable that cite ≥2 independent sources as
  joint support (e.g. "log spike X matches open issue Y").
- 0 = none; 1 = 1-2 such claims; 2 = 3-5; 3 = the central conclusion is only
  reachable by correlation.

### F6. error_recovery_demand

How much the task requires diagnosing and routing around its own failures
(dead-end queries, unavailable tools, empty results) rather than following a
happy path.

- Measure (retrospective): count of failed/empty tool results that the agent
  had to interpret and re-plan around; note whether recovery was correct.
  (prospective): count of scope items dependent on tools with known-uncertain
  availability.
- 0 = happy path; 1 = 1-2 trivial retries; 2 = several re-plans; 3 = the task
  outcome hinged on correctly diagnosing a failure.

### F7. output_structure_complexity

Rigor of the required deliverable format (tables with row-level evidence
contracts, multi-artifact consistency, exact markers).

- Measure: count distinct structural contracts the deliverable must satisfy
  (evidence-table row shape, cross-artifact consistency wiki↔Slack, hard
  format markers, count limits).
- 0 = free prose; 1 = one simple structure; 2 = 2-3 contracts; 3 = ≥4
  contracts or machine-parsed output.

### F8. latency_tolerance (routing modifier, not a difficulty feature)

Whether the task can afford the deep tier's single-stream latency and
scheduling (cluster window required).

- Measure: from the task's deadline/interactivity — 0 = interactive/minutes;
  1 = hour-scale; 2 = overnight batch; 3 = fully deadline-free.
- Low tolerance VETOES deep regardless of other scores.

## Decision output

Per task, emit:

```yaml
task: <name>
features:              # 0-3 each
  reasoning_depth:     TBD
  context_breadth:     TBD
  tool_chain_length:   TBD
  ambiguity:           TBD
  cross_source_synthesis: TBD
  error_recovery_demand:  TBD
  output_structure_complexity: TBD
  latency_tolerance:   TBD
difficulty_score: TBD  # weighted sum, weights below
tier: TBD              # deep | broad | either
confidence: TBD        # low | medium | high
```

Provisional decision rule (to be FITTED against the graded curriculum + the
matched 30B/353B packets — treat as a prior, not a result):

- `difficulty_score = 2*F1 + 1.5*F5 + 1*F2 + 1*F4 + 0.5*F3 + 0.5*F6 + 0.5*F7`
  (max 21). F1 and F5 are weighted highest on the hypothesis that deep
  reasoning and cross-source correlation are where a 353B most outperforms a
  30B, while long tool chains (F3) are executor-bound, not brain-bound.
- `latency_tolerance ≤ 0` → **broad** (veto).
- `difficulty_score ≥ 12` → **deep**; `≤ 6` → **broad**; else **either**
  (route by availability/cost).
- Thresholds and weights are TBD pending the measured 30B-vs-353B grade deltas.

## Measurement table (populate during the rubric run)

Prospective estimates may be filled from the job prompts now; retrospective
values and grade deltas only after runs on each tier.

| Task | F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | Score | 30B grade | 353B grade | Δ | Tier (fitted) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| orient | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| reposweep | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| splunk | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| apps | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| improve | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| (held-out deep tasks) | | | | | | | | | | | | | |
