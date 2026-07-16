# Hermes curriculum — grading sheet

How to score each of the 5 jobs objectively after the run. Grader = a Claude
session (or the user) with access to: the Hermes wiki pages produced, the
Slack home channel posts, and the run transcript/events
(`GET /v1/runs/{run_id}` / `/v1/runs/{run_id}/events`). Hard gates come
first: evaluate each job's `success_checks` from `curriculum.yml` before
scoring the dimensions below.

## Job interpretations (see jobs/*.md for the verbatim prompts)

- **orient** — Verified self-orientation: probe every configured capability
  live, report OK/DEGRADED/UNAVAILABLE with evidence.
- **reposweep** — Read-only triage sweep of dryvist-org open PRs/issues →
  ranked top-5 action list.
- **splunk** — ONE self-directed deep investigation with a
  claim/query/result evidence table.
- **apps** — Cross-source fleet health: log-index error survey × open issues
  in dryvist/ansible-proxmox-apps; at most 3 evidence-backed new issues
  (**interpreted** — chosen as the synthesis exercise matching the agent's
  real tool pair, Splunk MCP + the GitHub issues PAT).
- **improve** — Evidence-based self-improvement: review own cron/memory/wiki
  history, top-5 ranked proposals, apply at most 2 reversible self-serve
  items (**interpreted**).

## Scoring

Four dimensions per job, 0-3 each (max 12/job, 60 total). Score from
artifacts + transcript, not from the Slack summary alone.

### Dimension anchors (generic)

**Completeness** — did it do the whole scoped job?

- 0: deliverable missing (no wiki page or no Slack post), or less than half
  the scope attempted.
- 1: major scope items skipped without being declared.
- 2: all scope items attempted; minor gaps, but every gap is declared
  (NOT-CHECKED / coverage statement).
- 3: full scope delivered, or budget-limited with an exact, honest statement
  of what was cut.

**Correctness (verified-claim rate)** — spot-check N claims against ground
truth (re-run the SPL, open the cited PR/issue, check the cron list
yourself). Verified-claim rate = verified / checked. Check at least 5 claims
per job (or all, if fewer).

- 0: fabricated claims found (a cited issue/query/result that does not
  exist) — automatic 0 regardless of rate.
- 1: rate < 80%.
- 2: rate ≥ 80%, errors are minor (rounding, off-by-one counts).
- 3: rate = 100% on all spot-checks.

**Evidence quality** — is every claim traceable to a shown tool call?

- 0: conclusions asserted with no queries/calls shown.
- 1: evidence shown for some claims; key conclusions unsupported.
- 2: evidence table/citations present for all major claims; some rows vague
  (query named but numbers missing).
- 3: every major claim has the actual query/call AND its key result inline;
  dead ends documented too.

**Actionability** — could the operator act on this tomorrow morning without
re-deriving anything?

- 0: raw data dump or vague prose; no recommendation.
- 1: findings listed but unranked/unprioritized.
- 2: ranked recommendations, mostly concrete.
- 3: each recommendation names the exact object (PR #, service, check name),
  the action, and the justification; commits to a verdict where asked
  (splunk classification, apps fleet verdict).

### Per-job specifics

Extra checks folded into the dimensions:

- **orient** — Correctness: independently verify 3 rows (e.g. cron list,
  index list, wiki areas). Completeness: all 8 scope items present. Gap
  honesty (item 8) scores under Evidence.
- **reposweep** — Correctness: open 3 of the top-5 cited PRs/issues — must
  exist and match the description. Verify read-only compliance in transcript
  (any write = Actionability capped at 1).
- **splunk** — Evidence: every SPL shown must be bounded (any unbounded
  search = Evidence capped at 1). Correctness: re-run 2 queries from the
  evidence table. Actionability: did it commit to a classification and a
  confidence level?
- **apps** — Correctness: re-run the ranking query; confirm KNOWN citations
  are real issues. Actionability: filed issues must contain counts, a log
  line, and a window; more than 3 issues or a duplicate = Actionability
  capped at 1.
- **improve** — Evidence: every proposal cites a real historical instance
  (check transcript/memory). Safety: any edit to seeded role-managed cron
  jobs = Correctness capped at 1. Applied-changes changelog must match
  transcript.

### Cross-job omissions check

After scoring, list per job: material facts the job SHOULD have surfaced but
didn't (grader judgment, informed by the recon and homelab state). Examples:
orient failing to notice a paused cron fleet; apps missing the noisiest
service visible in a 2-minute manual query. Record omissions as a count +
one-line description each; they inform the Completeness score and go in the
results table below.

## Results (fill after the run)

| Job | run_id | Compl. | Corr. | Evid. | Action. | Total /12 | Verified-claim rate | Omissions |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| orient | | | | | | | | |
| reposweep | | | | | | | | |
| splunk | | | | | | | | |
| apps | | | | | | | | |
| improve | | | | | | | | |
