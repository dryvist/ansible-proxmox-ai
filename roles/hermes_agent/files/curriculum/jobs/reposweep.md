# Curriculum job: reposweep — GitHub triage sweep of the dryvist org

You are running as a one-off submitted job (not a cron). This is a graded
breadth exercise: sweep the dryvist GitHub org's open work and produce a
triage table an operator can act on tomorrow morning.

Use your GitHub issues capability (the dryvist/github-issues skill and its
token). If the token is missing or unauthorized, do NOT fake results: report
the exact error as the finding, write the wiki page documenting the gap, post
it to Slack, and stop — that is a valid (gradeable) outcome.

## Objective

An accurate, current snapshot of open PRs and issues across the dryvist org,
ranked by what most needs a human decision.

## Scope

1. Enumerate the org's repos with open PRs or open issues (skip archived).
2. **Open PRs**: for each, capture repo, number, title, age, draft?, CI state
   if visible, and mergeable/blocked. Flag PRs older than 7 days as stale.
3. **Open issues**: count per repo; list the 10 oldest untouched issues
   (no comment/label activity in 30+ days) org-wide.
4. **Cross-repo signals**: note any repo with >5 open PRs, and any Renovate/
   bot PRs that have been open >7 days (dependency updates going stale).
5. Rank a **top-5 action list**: the five items where one human decision
   unblocks the most (merge X, close stale Y, answer question in Z...). One
   line of justification each, grounded in what you observed.

## Constraints

- READ-ONLY: do not comment on, label, close, or edit any PR or issue in this
  job. You are producing a report, not acting on it.
- Paginate honestly: if you truncate (rate limits, budget), say exactly what
  was and wasn't covered.

## Deliverable

- Wiki page `curriculum/reposweep`: coverage statement (repos swept / skipped
  and why), the PR table, the stale-issue list, cross-repo signals, top-5
  action list.
- Slack home channel post: the top-5 action list plus totals (repos swept,
  open PRs, open issues), ≤15 lines.

## Success criteria

- Numbers are internally consistent (totals match the tables).
- Every top-5 item cites a real, current PR/issue number that exists.
- Coverage statement is truthful about anything skipped.

## Budget (hard)

At most 40 agent turns and about 60 minutes. On budget exhaustion, publish
partial results with an explicit coverage statement. Never retry a failing
API call more than twice.
