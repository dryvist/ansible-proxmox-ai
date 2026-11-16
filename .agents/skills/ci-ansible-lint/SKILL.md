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
  against the budgets in `.token-limits.yaml`.
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

`roles/hermes_agent/templates/config.yaml.j2` was over the 4000-token `*.j2` budget.

- Wrong (seen 11-15): rewriting the template shorter (-50/+33 lines). The rendered config changed and the
  Role Self-Checks asserts on its defaults failed.
- Right: move one self-contained section, unchanged, into its own partial next to the template, for example
  `templates/config/gateway.yaml.j2`, and put `{% include 'config/gateway.yaml.j2' %}` at the same place and
  indentation. The rendered output is byte-identical, so the role's pytest still passes, and each file is under budget.

## Never

- Add `skip_ansible_lint`, `# noqa`, `# nosec`, `tflint-ignore`, or any lint disable; delete, skip, or weaken
  a test or assertion; add a script; hard-code a value that belongs in a variable.
- Raise a `.token-limits.yaml` budget or exclude a file to fit it.

## Known causes

- 2026-11-15, run 43700947427: a template was rewritten shorter instead of split; Role Self-Checks failed after.
