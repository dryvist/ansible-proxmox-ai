---
name: ci-molecule
description: Fix failures in the Molecule scenario checks that converge and verify roles in containers.
triggers:
  - Molecule
  - molecule
---

# Molecule

## What it checks

`ci-gate.yml` jobs `molecule-hosted` ("Molecule on ubuntu-latest") and `molecule-hermes-ui` ("Molecule
hermes_ui runner") call `JacobPEvans/.github` `_molecule.yml`: "Select Molecule scenarios" discovers
the selected scenarios, then "Molecule (<scenario>)" runs each. `hermes_ui` runs on its own runner;
`agent_guest` additionally installs nix. The test command is fixed: `molecule test -s "$SCENARIO"`.

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force   # once per fresh worktree
.github/scripts/discover-molecule-scenarios.sh       # list scenario names
molecule test -s <scenario>                          # full cycle: create, converge, idempotence, verify, destroy
```

## Cheapest fix first

No autofix exists. Rerun the failing scenario's step if useful
(`molecule converge -s <scenario>`, then `molecule verify -s <scenario>`), but the gate is the full
`molecule test` cycle. Fix the role task or the scenario's verify assertion that failed.

## Never

- Delete, skip or weaken a test or assertion, including `verify.yml` checks.
- Skip stages: `molecule test` is the check; `converge`/`verify` alone are debugging aids.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, a lint disable, a Terraform
  `data "external"`, or a script; hard-code a value that belongs in a variable.

## Known causes

none recorded yet
