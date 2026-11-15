---
name: ci-selection-jobs
description: Fix failures of the change-detection and contract-scope selection jobs (Detect Changes, Data Contract Scope) on this repo's pull requests.
triggers:
  - Detect Changes
  - Data Contract Scope
---

## What it checks

Two `ci-gate.yml` jobs select what runs; neither asserts repo content.

- **Detect Changes** (the `changes` job, and the same-named job inside the `ci` job) — `dorny/paths-filter`
  over the `molecule_scenario_filters` and `molecule_contract_filters` blocks in `ci-gate.yml`.
- **Data Contract Scope** (the `contract-scope` job) — `python .github/scripts/select-contract-scope.py`
  maps changed paths to pytest targets, Ansible contract tests, router playbooks, and inventory.

## Reproduce locally

```bash
git fetch origin develop
git diff --name-only "$(git merge-base origin/develop HEAD)" HEAD > /tmp/changed-paths
python .github/scripts/select-contract-scope.py \
  --paths-file /tmp/changed-paths \
  --base-ref "$(git merge-base origin/develop HEAD)" \
  --head-ref HEAD --output /tmp/selection.out
cat /tmp/selection.out
```

Detect Changes needs the PR diff from the GitHub API; triage it by validating the two filter blocks in
`ci-gate.yml` as YAML and re-reading the mapping entries the log names.

## Cheapest fix first

Fix the YAML or the selector mapping the failure names. An unmapped path is not itself an error: on a PR
into develop the selector widens unmapped contract paths to the full suite by design and does not fail.

## Never

- Delete a filter or mapping entry, or disable a job, to stop the failure.
- Edit the pinned refs of the shared reusable workflows.
- Add `# noqa`, `# nosec`, `tflint-ignore`, or any lint disable; delete, skip, or weaken a test or
  assertion; add a script; hard-code a value that belongs in a variable.

## Known causes

none recorded yet
