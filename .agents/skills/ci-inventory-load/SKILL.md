---
name: ci-inventory-load
description: Fix failures in the Verify Inventory Load check that loads the mapped tofu inventory fixture.
triggers:
  - Verify Inventory Load
---

# Verify Inventory Load

## What it checks

`_data-contract.yml` job "Verify Inventory Load" runs the `inventory-load-contract` composite action:
it installs Ansible and the Galaxy dependencies, then proves `inventory/load_tofu.yml` resolves the
shared `inventory_resolve` role and groups the fixture correctly via two test playbooks.

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force   # `install`, not `collection install`
export TOFU_INVENTORY_PATH=$PWD/tests/inventory_load/tofu_inventory.json
export PROXMOX_SSH_KEY_PATH=/dev/null ANSIBLE_HOST_KEY_CHECKING=false
ansible-playbook tests/inventory_load/verify_inventory.yml -i inventory/hosts.yml -c local
ansible-playbook tests/inventory_load/test_ssh_probe_result_selection.yml -i inventory/hosts.yml -c local
```

## Cheapest fix first

No autofix exists. A failure means the mapping in `inventory/load_tofu.yml` or the fixture in
`tests/inventory_load/` drifted; the play output names the host or group that mismatched.

## Never

- Point `TOFU_INVENTORY_PATH` at anything but the checked-in fixture in a test run.
- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"` or a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
