---
name: ci-role-self-checks
description: Fix failures in the Role Self-Checks job, the focused pytest pass over tests directories.
triggers:
  - Role Self-Checks
  - pytest
---

# Role Self-Checks

## What it checks

`_data-contract.yml` job "Role Self-Checks" runs `python -m pytest` over the Data Contract Scope
selector's targets, excluding `tests/llm_router/` (its own contract job) and
`tests/qdrant_llamaindex/` (needs live services). Dependencies come from the `router-deps` composite
action (`.venv` from `.github/requirements-ci.txt` plus Galaxy content).

## Reproduce locally

```bash
pip install -r .github/requirements-ci.txt
ansible-galaxy install -r requirements.yml --force   # once per fresh worktree
python -m pytest <failing test file> -q              # start from the failed test file
python -m pytest tests/<dir> -q -n auto --ignore=tests/llm_router --ignore=tests/qdrant_llamaindex
```

The job sets CI placeholder env values (`LLM_ROUTER_MASTER_KEY`, `LLM_LARGE_BEARER_TOKEN`,
`PROXMOX_SUBDOMAIN`) exactly as `_data-contract.yml` does.

## Cheapest fix first

No autofix exists. Fix the role or test the failing assertion guards; pytest output names the
assertion and file.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
