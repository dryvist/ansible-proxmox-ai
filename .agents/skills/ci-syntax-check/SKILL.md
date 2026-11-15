---
name: ci-syntax-check
description: Fix failures in the Syntax Check job that syntax-checks playbooks and runs repo-guard test plays.
triggers:
  - Syntax Check
  - ansible-playbook --syntax-check
---

# Syntax Check

## What it checks

`_data-contract.yml` job "Syntax Check" (reached as `ci-gate.yml` `data-contract`), called with CI
placeholder env values, syntax-checks every `playbooks/*.{yml,yaml}`, then runs the repo-guard and
contract test playbooks under `tests/repo_guards/`, `tests/openbao_secrets/`, and sibling `tests/`
directories (each step's exact command is in `.github/workflows/_data-contract.yml`).

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force   # once per fresh worktree
for playbook in playbooks/*.{yml,yaml}; do
  [ -f "$playbook" ] && ansible-playbook "$playbook" --syntax-check
done
# Rerun the failing step's command verbatim from _data-contract.yml, e.g.
ansible-playbook tests/repo_guards/test_env_guards_actually_fire.yml -i inventory/hosts.yml -c local
```

## Cheapest fix first

No autofix exists. The failure names the playbook or task file and line; fix that YAML or task.
For a guard-playbook failure, the guarded behavior regressed — fix the role, not the guard.

## Never

- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore` or any other lint disable.
- Add a Terraform `data "external"`.
- Add a script.
- Hard-code a value that belongs in a variable.

## Known causes

none recorded yet
