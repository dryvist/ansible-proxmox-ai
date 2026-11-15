---
name: ci-data-contract
description: Fix failures of the Data Contract jobs (Syntax Check, Role Self-Checks, Verify Inventory Load) on this repo's pull requests.
triggers:
  - Syntax Check
  - Role Self-Checks
  - Verify Inventory Load
---

## What it checks

The `data-contract` job in `.github/workflows/ci-gate.yml` calls the local `.github/workflows/_data-contract.yml`.
The Data Contract Scope job chooses which steps run on a PR into develop; PRs into main and pushes run all of them.

- **Syntax Check** — `ansible-playbook --syntax-check` over every `playbooks/*.yml` with static placeholder
  env values (not real secrets).
- **Role Self-Checks** — `python -m pytest` over the selected targets under `tests/`, excluding
  `tests/llm_router` (own job) and `tests/qdrant_llamaindex` (needs live services).
- **Verify Inventory Load** — the `.github/actions/inventory-load-contract` action proves the published tofu inventory maps onto this repo's groups.

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force
ansible-playbook playbooks/site.yml --syntax-check
TOFU_INVENTORY_PATH=$PWD/tests/inventory_load/tofu_inventory.json \
  ansible-playbook tests/inventory_load/verify_inventory.yml \
  -i inventory/hosts.yml -c local
python -m pytest tests/<directory> -q -n auto
```

Python and Ansible dependencies come from `.github/requirements-ci.txt` (the `router-deps` composite action installs them into `.venv`).

## Cheapest fix first

Run only the failing step — the single playbook's `--syntax-check`, the single pytest file, or the
inventory-load playbook above — and fix the smallest thing the failure names.

## Never

- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, or any lint disable; delete, skip, or weaken
  a test or assertion, including xfailing or deselecting a pytest test.
- Replace the static placeholder env values with real credentials.
- Add a script; hard-code a value that belongs in a variable.

## Known causes

none recorded yet
