---
name: ci-molecule-select
description: Fix failures in the Select Molecule runner groups job that partitions scenarios by runner.
triggers:
  - Select Molecule runner groups
  - Partition Molecule scenarios
---

# Select Molecule runner groups

## What it checks

`ci-gate.yml` job `molecule-select` ("Select Molecule runner groups") calls
`.github/workflows/_molecule-select.yml`, job "Partition Molecule scenarios". On a full suite or an
empty selection it discovers scenarios with `.github/scripts/discover-molecule-scenarios.sh`, then
splits them into hosted scenarios and the `hermes_ui` runner scenario.

## Reproduce locally

```bash
.github/scripts/discover-molecule-scenarios.sh   # prints the scenario JSON array
```

The partition step is two `jq` filters over that array; feed it the same JSON to reproduce either
output.

## Cheapest fix first

No autofix exists. A discovery failure means a `molecule/<name>/molecule.yml` path has a name
failing `^[a-z0-9_]+$` or no scenario was found; rename the scenario directory to match the rule.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a scenario list — the matrix is derived from disk on purpose.

## Known causes

none recorded yet
