---
name: ci-lane-alias-parity
description: Fix failures in the Lane alias parity check against the shared lane contract.
triggers:
  - Lane alias parity
  - Check lane aliases
  - lane-alias-parity
---

# Lane alias parity

## What it checks

`ci-gate.yml` job `lane-alias-parity` calls `.github/workflows/_lane-alias-parity.yml`: "Export lane
alias names" runs `tests/llm_router/lane_alias_names.py` and `scripts/generate_servable_aliases.py`,
then "Shared lane alias parity" calls `jpe-ansible/homelab-contracts` `alias-parity.yml`, job
"Check lane aliases", which validates the exported names against `lanes.json` and the router names.

## Reproduce locally

```bash
python3 tests/llm_router/lane_alias_names.py > lane-alias-parity-apai.json
python3 scripts/generate_servable_aliases.py -o servable-aliases.json
git clone --depth 1 https://github.com/jpe-ansible/homelab-contracts /tmp/homelab-contracts
python3 /tmp/homelab-contracts/.github/scripts/lane-alias-parity.py \
  --lanes /tmp/homelab-contracts/ansible/roles/llm_roles/files/lanes.json \
  --names lane-alias-parity-apai.json --router-names servable-aliases.json
```

## Cheapest fix first

No autofix exists. The names are generated: fix the source in `tests/llm_router/` or the
`llm-models.d/` registry entry the generator reads, then regenerate.

## Never

- Hand-edit the generated JSON or the shared `lanes.json`.
- Add an alias anywhere outside the registry's `stable_aliases` (alias rule, hard).
- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, a lint disable, a Terraform
  `data "external"`, or a script; hard-code a value that belongs in a variable.

## Known causes

none recorded yet
