---
name: ci-conformance
description: Fix failures of the Agent platform conformance pre-commit check on this repo's pull requests.
triggers:
  - Agent platform conformance
  - conformance
  - check-jsonschema
---

## What it checks

The `conformance` job in `.github/workflows/ci-gate.yml` calls the shared `dryvist/.github` workflow
`agent-platform-conformance.yml` (pinned SHA), whose single job runs the shared pre-commit template on every tracked file:

- no prompt files (`prompt*.md`, `*.prompt`, `system_prompt*`) under `roles/`;
- no banned capability-name tokens in paths or file contents (the pygrep patterns live in the shared template cloned below);
- every `roles/**/*.yml` validates against the agent-platform schema: a `prompt` or `system_prompt`
  string value spans at most five lines.

## Reproduce locally

```bash
pip install pre-commit==4.5.1 check-jsonschema==0.37.2
gh repo clone dryvist/.github .agent-platform -- --depth 1
mkdir -p precommit/configs
cp .agent-platform/precommit/configs/agent-platform.schema.json precommit/configs/
pre-commit run --config .agent-platform/precommit/templates/agent-platform.yaml --all-files
```

CI checks out that repository at the SHA pinned in `ci-gate.yml`; a default-branch clone is close enough
to triage.

## Cheapest fix first

No hook auto-fixes. Edit the flagged file: move a prompt file out of `roles/`, rename the flagged path or
identifier, or shorten the prompt value the schema rejected. This repo's own `.pre-commit-config.yaml`
does not run the template — the check exists only in the shared workflow.

## Never

- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, or any lint disable; delete, skip, or
  weaken a test or assertion; add a script; hard-code a value that belongs in a variable.
- Rename or split role YAML files only to dodge the schema's file glob instead of fixing the prompt the
  schema rejected.

## Known causes

none recorded yet
