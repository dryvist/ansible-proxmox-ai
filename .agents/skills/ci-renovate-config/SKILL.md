---
name: ci-renovate-config
description: Fix failures in the Renovate Config Validate check that validates renovate.json strictly.
triggers:
  - Renovate Config Validate
  - renovate-config-validator
---

# Renovate Config Validate

## What it checks

`ci-gate.yml` job `ci` calls `_ansible-ci.yml`; its `renovate_config` job calls
`JacobPEvans/.github` `renovate-config-validate.yml`, job "Validate". It runs
`renovate-config-validator --strict` on `renovate.json` with Renovate 44.222.0, so deprecation and
migration warnings fail the check too.

## Reproduce locally

```bash
RENOVATE_CONFIG_FILE=renovate.json \
  npx --yes --package renovate@44.222.0 -- renovate-config-validator --strict
```

## Cheapest fix first

No autofix exists. The validator error names the option or key; fix `renovate.json` directly — a
renamed or removed option is usually a one-key edit following the migration note in the message.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
