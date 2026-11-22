---
name: ci-ansible-lint
description: Fix failures of the shared Ansible CI lint checks (Ansible Lint, Token Limits, Renovate Config Validate) on this repo's pull requests.
triggers:
  - Ansible Lint
  - ansible-lint
  - Token Limits
  - Renovate Config Validate
---

# ci-ansible-lint

## What it checks

The `ci` job in `.github/workflows/ci-gate.yml` calls the shared `JacobPEvans/.github` workflow
`_ansible-ci.yml@v1` (Molecule off). On this repo's PRs it runs:

- **Ansible Lint** — whole-repo `ansible-lint` with this repo's `.ansible-lint` and `.yamllint`, after
  installing the `requirements.yml` Galaxy content.
- **Token Limits** — the shared `check-token-limits.py` counts the PR's own changed files with tiktoken
  against the budgets in `.token-limits.yaml`. Its log names each file over budget with its count; the PR author
  or the CI-fix agent reads it and splits that file.
- **Renovate Config Validate** — when a Renovate config file changed: `renovate-config-validator --strict`
  on `renovate.json` at a pinned Renovate version.

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force
ansible-lint
```

CI lints the whole repo; the pre-commit `ansible-lint` hook checks staged files only, so a locally clean commit can still fail here.

## Cheapest fix first

1. `ansible-lint --fix` rewrites formatting-only findings (yaml, line length); re-run and fix the rest by hand.
2. Token Limits: a size failure is fixed by moving, not by removing. The total content after the change equals
   the content before; only its place changes. Split the flagged file into smaller well-named files (a budget
   exists so an agent reads one thing, not six), then run every check the moved content feeds, such as the
   role's `python -m pytest` tests and `ansible-lint`.
3. Renovate Config Validate: fix the JSON property the validator names.

## Worked example: Token Limits

A role template `templates/<name>.yaml.j2` is over the `*.j2` budget.

- Wrong: rewriting the template shorter. The rendered output changes and the role's tests that assert on it fail.
- Right: move one self-contained section, unchanged, into its own partial next to the template, for example
  `templates/<name>/<section>.yaml.j2`, and put `{% include '<name>/<section>.yaml.j2' %}` at the same place and
  indentation. The rendered output is byte-identical, so the role's pytest still passes, and each file is under budget.

## Never

- Add `skip_ansible_lint`, `# noqa`, `# nosec`, `tflint-ignore`, or any lint disable; delete, skip, or weaken
  a test or assertion; add a script; hard-code a value that belongs in a variable.
- Raise a `.token-limits.yaml` budget or exclude a file to fit it.

## Known causes

- A template rewritten shorter instead of split: the role's tests fail after the fix.
