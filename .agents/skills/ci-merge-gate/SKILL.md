---
name: ci-merge-gate
description: Resolve Merge Gate and Result Validation failures by finding the child check that actually failed.
triggers:
  - Merge Gate
  - Result Validation
---

## What it checks

The `gate` job ("Merge Gate") in `.github/workflows/ci-gate.yml` aggregates
every other job's result through the shared `ci-gate-aggregate.mts` script;
it runs no content check of its own. "ci / Result Validation" is the same
aggregator inside the shared `_ansible-ci.yml@v1` workflow for its own jobs.

A red aggregate means exactly one thing: a child check failed or was skipped
where a skip is not allowed. The set of allowed skips is the
`CI_GATE_ALLOWED_SKIPS` list in the workflow.

## Reproduce locally

Not reproducible — pure aggregation. Open the failed run's summary, read the
failed child check's name, and follow that check's own skill under
`.agents/skills/`.

## Cheapest fix first

Fix the real failing child check. Nothing inside the gate itself is fixable.

## Never

- Edit `CI_GATE_ALLOWED_SKIPS`, the `needs:` list, or any `if:` condition to
  turn the gate green.
- Retry the run hoping it passes; report which child failed instead.
- Add `# noqa`, `# nosec`, `tflint-ignore`, or any lint disable; delete,
  skip, or weaken a test or assertion; add a script; hard-code a value that
  belongs in a variable.

## Known causes

none recorded yet
