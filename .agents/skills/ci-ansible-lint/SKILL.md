---
name: ci-ansible-lint
description: Fix failures of the shared Ansible CI lint checks (Ansible Lint, Token Limits, Renovate Config Validate) on this repo's pull requests.
triggers:
  - Ansible Lint
  - ansible-lint
  - Token Limits
  - Renovate Config Validate
---

## What it checks

The `ci` job in `.github/workflows/ci-gate.yml` calls the shared `JacobPEvans/.github` workflow
`_ansible-ci.yml@v1` (Molecule off). On this repo's PRs it runs:

- **Ansible Lint** — whole-repo `ansible-lint` with this repo's `.ansible-lint` and `.yamllint`, after
  installing the `requirements.yml` Galaxy content.
- **Token Limits** — the shared `check-token-limits.py` counts the PR's own changed files with tiktoken
  against the budgets in `.token-limits.yaml`.
- **Renovate Config Validate** — when a Renovate config file changed: `renovate-config-validator --strict`
  on `renovate.json` at a pinned Renovate version.

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force
ansible-lint
```

CI lints the whole repo; the pre-commit `ansible-lint` hook checks staged files only, so a locally clean
commit can still fail here.

## Cheapest fix first

1. `ansible-lint --fix` rewrites formatting-only findings (yaml, line length); re-run and fix the rest by hand.
2. Token Limits: split the flagged file into smaller well-named files; a budget exists so an agent reads one
   thing, not six.
3. Renovate Config Validate: fix the JSON property the validator names.

## Never

- Add `skip_ansible_lint`, `# noqa`, `# nosec`, `tflint-ignore`, or any lint disable; delete, skip, or weaken
  a test or assertion; add a script; hard-code a value that belongs in a variable.
- Raise a `.token-limits.yaml` budget or exclude a file to fit it.

## Known causes

none recorded yet
