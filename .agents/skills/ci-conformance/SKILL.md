---
name: ci-conformance
description: Fix failures in the Agent platform conformance check over naming and YAML prompt-schema rules.
triggers:
  - Agent platform conformance
  - Agent Platform Conformance
  - check-jsonschema
---

# Agent platform conformance

## What it checks

`ci-gate.yml` job `conformance` calls `dryvist/.github` `agent-platform-conformance.yml`, job
"Agent platform conformance". It checks out `dryvist/.github` at the pinned commit and runs
`pre-commit run --config .agent-platform/precommit/templates/agent-platform.yaml --all-files`:
prompt-file filename bans, banned path names and identifiers, and
`check-jsonschema` against `agent-platform.schema.json` for `roles/**/*.yml`.

## Reproduce locally

```bash
python -m pip install pre-commit==4.5.1 check-jsonschema==0.37.2
git clone https://github.com/dryvist/.github /tmp/agent-platform
git -C /tmp/agent-platform checkout 198f25d695819a48e73f5a7723b47c5bd4839a7c
mkdir -p precommit/configs
cp /tmp/agent-platform/precommit/configs/agent-platform.schema.json precommit/configs/
pre-commit run --config /tmp/agent-platform/precommit/templates/agent-platform.yaml --all-files
```

## Cheapest fix first

No autofix exists; the hooks are fail-by-name, so rename the flagged file, path or identifier.
Schema failures point at the exact property in the flagged roles YAML.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
