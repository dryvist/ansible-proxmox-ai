---
name: ci-contract-scope
description: Fix failures in the Data Contract Scope check that selects which contract tests a PR runs.
triggers:
  - Data Contract Scope
  - select-contract-scope
---

# Data Contract Scope

## What it checks

`ci-gate.yml` job `contract-scope` ("Data Contract Scope") runs
`.github/scripts/select-contract-scope.py` over the PR's changed paths and exports `pytest_targets`,
`ansible_tests`, `llm_router_playbooks`, `run_inventory`, and `run_selector_checks` for the Data
Contract workflow. On pushes to `main`/`develop` and PRs into `main` it selects the full suite.

## Reproduce locally

```bash
git fetch origin develop
git diff --name-only origin/develop...HEAD > /tmp/changed-paths
python .github/scripts/select-contract-scope.py \
  --paths-file /tmp/changed-paths \
  --base-ref "$(git merge-base origin/develop HEAD)" \
  --head-ref "HEAD" \
  --output /tmp/scope.out
python .github/scripts/select-contract-scope.py --report   # needs the pinned PyYAML in requirements-ci.txt
```

## Cheapest fix first

No autofix exists. Reproduce the selection first: an unexpected selection is usually a mapping gap in
the selector; an actual crash is a bug in `.github/scripts/select-contract-scope.py` or its inputs.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
