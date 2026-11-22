---
name: ci-llm-router-contract
description: Fix failures of the LLM Router Contract jobs (Router contract shards and the Registry single-source contract) on this repo's pull requests.
triggers:
  - Router contract
  - Registry single-source contract
  - llm-router-contract
---

# ci-llm-router-contract

## What it checks

The Data Contract workflow (`_data-contract.yml`) calls the local `.github/workflows/_llm-router-contract.yml`, three job families:

- **Router contract dependencies** — proves every selected playbook is registered in `.github/router-contracts.yml` and the router lockfile resolves.
- **Router contract: shards** — each shard runs its registered playbooks via `ansible-playbook -i localhost, -c local`
  with static placeholder env values.
- **Registry single-source contract** — `pytest tests/llm_router/ -q`: an id, alias, tier, or secret key re-typed outside `llm-models.d/` fails here.

## Reproduce locally

```bash
python -m venv .venv
.venv/bin/pip install -r .github/requirements-ci.txt
.venv/bin/ansible-galaxy install --force -r requirements.yml

.venv/bin/python -m pytest tests/llm_router/ -q
.venv/bin/ansible-playbook tests/llm_router/test_<name>.yml -i localhost, -c local
```

Export the same placeholders the workflow sets (static non-secrets): `LLM_ROUTER_MASTER_KEY`, `LLM_LARGE_BEARER_TOKEN`, `PROXMOX_SUBDOMAIN`.

## Cheapest fix first

Run the single failing pytest file or playbook and read its assertion. For a registry scan failure the fix lives in the
`llm-models.d/` registry entry — see the registry and alias rules in AGENTS.md.

## Never

- Add `# noqa`, `# nosec`, or any lint disable; delete, skip, or weaken a test or assertion; xfail or deselect
  a pytest test; add a script; hard-code a value that belongs in a variable.
- Add a model id, alias, tier, or secret key field outside the root `llm-models.d/` registry (in `roles/llm_router/` or any other role).
- Register an alias as its own `model_list` deployment duplicating a physical entry's config; aliases live in `stable_aliases` only.

## Known causes

none recorded yet
