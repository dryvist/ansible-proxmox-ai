---
name: ci-merge-gate
description: Interpret Merge Gate and Result Validation failures by routing to the underlying failed check.
triggers:
  - Merge Gate
  - Result Validation
---

# Merge Gate

## What it checks

`ci-gate.yml` job `gate` ("Merge Gate") aggregates every sibling job with
`JacobPEvans/.github`'s `ci-gate-aggregate.mts`; `_ansible-ci.yml`'s `gate` job does the same for the
shared checks as "Result Validation". Neither runs any check of its own: it fails because a needed
job failed or was skipped outside the allowed skips.

## Reproduce locally

None — open the workflow run summary and read which `needs` entry failed or was skipped
unexpectedly, then follow that check's own `ci-*` skill.

## Cheapest fix first

There is nothing to fix in the gate itself. Fix the underlying failed check; a skipped required job
means its `if` condition evaluated false — trace why before assuming the gate is wrong.

## Never

- Edit the aggregator, the `needs` list, or the allowed-skips list.
- Add `continue-on-error` anywhere to turn a failing check green.
- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, a lint disable, a Terraform
  `data "external"`, or a script; hard-code a value that belongs in a variable.

## Known causes

none recorded yet
