---
name: ci-llm-router-contract
description: Fix failures in the LLM Router Contract jobs, the llm_router playbooks and registry pytest pass.
triggers:
  - LLM Router Contract
  - Router contract
  - Registry single-source contract
---

# LLM Router Contract

## What it checks

`_data-contract.yml` calls `.github/workflows/_llm-router-contract.yml`: "Router contract
dependencies" installs the cached `.venv`; "Router contract: <shard>" runs each registered playbook
from `.github/router-contracts.yml` with `ansible-playbook`; "Registry single-source contract" runs
`python -m pytest tests/llm_router/ -q`, where `test_registry_retype_scan.py` enforces that every
model name, alias, and tier is written once in `llm-models.d/`.

## Reproduce locally

```bash
python -m venv .venv && .venv/bin/pip install -r .github/requirements-ci.txt
.venv/bin/ansible-galaxy install --force -r requirements.yml
.venv/bin/ansible-playbook <failing playbook> -i localhost, -c local
.venv/bin/python -m pytest tests/llm_router/ -q
```

## Cheapest fix first

No autofix exists. Fix the flagged registry entry in `llm-models.d/`, the role projection it fails
against, or the playbook contract — the pytest output names the offending literal.

## Never

- Add a model id, alias, or secret key field anywhere outside `llm-models.d/` (registry rule, hard).
- Delete, skip or weaken a test or assertion, especially `test_registry_retype_scan.py`.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"` or a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
