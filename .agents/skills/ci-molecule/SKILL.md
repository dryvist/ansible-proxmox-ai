---
name: ci-molecule
description: Fix failures of the Molecule jobs (scenario selection, partition, and per-scenario test runs on both runners) on this repo's pull requests.
triggers:
  - Molecule
  - molecule
  - Select Molecule scenarios
  - Partition Molecule scenarios
  - Select Molecule runner groups
---

# ci-molecule

## What it checks

Three `ci-gate.yml` jobs run Molecule on pull requests:

- **Select Molecule runner groups** (the `molecule-select` job, local `_molecule-select.yml`) — partitions
  the selected scenarios between the two runners.
- **Molecule on ubuntu-latest** and **Molecule hermes_ui runner** — both call the shared `JacobPEvans/.github` workflow
  `_molecule.yml@v1`: a "Select Molecule scenarios" job discovers scenarios, then one "Molecule (scenario)" matrix job runs per scenario.

A PR into develop runs only the scenarios mapped to its changed paths (the `molecule_scenario_filters` map in `ci-gate.yml`); PRs into main run all.

## Reproduce locally

```bash
ansible-galaxy install -r requirements.yml --force
molecule test -s <scenario>
```

Step through individual phases (`molecule converge` / `molecule verify` / `molecule destroy`) as the Testing
section of AGENTS.md documents. Scenario directories live under `molecule/`; the `agent_guest` scenario
evaluates a NixOS flake and needs Nix on the controller.

## Cheapest fix first

Run the failing scenario alone and read the failing phase's task output. `molecule verify` failures are
assertions about the converged state — fix the role, not the verify step.

## Never

- Delete, skip, or weaken a molecule verify task or assertion; add `# noqa`, `# nosec`, `skip_ansible_lint`,
  `tflint-ignore`, or any lint disable.
- Edit the scenario filter map in `ci-gate.yml` to deselect a failing scenario; add a script; hard-code a
  value that belongs in a variable.

## Known causes

none recorded yet
