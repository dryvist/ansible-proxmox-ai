---
name: ci-detect-changes
description: Debug the Detect Changes check that maps a PR's changed paths onto checks and Molecule scenarios.
triggers:
  - Detect Changes
---

# Detect Changes

## What it checks

`ci-gate.yml` job `changes` and `_ansible-ci.yml` job `changes` both call `JacobPEvans/.github`
`_ansible-changes.yml` ("Detect Changes"). dorny/paths-filter applies the `molecule_scenario_filters`
and `molecule_contract_filters` maps defined in `ci-gate.yml` to the PR diff and exports which
scenario matrix and contract scope run.

## Reproduce locally

```bash
git fetch origin develop
git diff --name-only origin/develop...HEAD
```

Compare the output against the filter maps in `.github/workflows/ci-gate.yml`; dorny/paths-filter
itself has no local equivalent.

## Cheapest fix first

No autofix exists. If a check unexpectedly ran or skipped, the changed paths did not match the
intended filter patterns. Change the content's paths only deliberately; editing the filter maps is
a CI change and needs explicit authorization.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
