---
name: ci-ansible-lint
description: Fix failures in the Ansible Lint check that runs ansible-lint over the whole repository.
triggers:
  - Ansible Lint
  - ansible-lint
---

# Ansible Lint

## What it checks

`.github/workflows/ci-gate.yml` job `ci` calls `JacobPEvans/.github` `_ansible-ci.yml`; its
`ansible-lint` job runs the `ansible/ansible-lint` action with no extra args, linting the whole
repository under the repo's `.ansible-lint` and `.yamllint` configs. Galaxy content installs to
`~/.ansible`, outside the linted tree.

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force   # once per fresh worktree
ansible-lint
```

## Cheapest fix first

1. `ansible-lint --fix` auto-fixes several rules (yaml formatting, jinja spacing).
2. `pre-commit run --all-files` covers yamllint, trailing whitespace, and end-of-file.
3. Edit the flagged task or file only after both pass cleanly.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
