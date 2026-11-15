---
name: ci-token-limits
description: Fix failures in the Token Limits check that budgets AI-read prose files with tiktoken.
triggers:
  - Token Limits
---

# Token Limits

## What it checks

`ci-gate.yml` job `ci` calls `_ansible-ci.yml`; its `token-limits` job calls `JacobPEvans/.github`
`_token-limits.yml`, job "Check". It runs `check-token-limits.py` (tiktoken, no API key) against the
budgets in `.token-limits.yaml`, scoped to the PR's own diff (`HEAD^1...HEAD`, hence fetch-depth 2).

## Reproduce locally

```bash
pip install --quiet tiktoken pyyaml
mkdir -p .gh-shared && curl -fsSL -o .gh-shared/scripts/check-token-limits.py \
  https://raw.githubusercontent.com/JacobPEvans/.github/v1/scripts/check-token-limits.py
python3 .gh-shared/scripts/check-token-limits.py   # keep .gh-shared untracked
```

## Cheapest fix first

No autofix exists. Trim the flagged prose file below its budget in `.token-limits.yaml`, or move it
to the pattern whose limit actually applies. Tighten prose; do not widen budgets.

## Never

- Delete, skip or weaken a test or assertion.
- Raise a limit in `.token-limits.yaml` to make a failure pass.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"` or a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
