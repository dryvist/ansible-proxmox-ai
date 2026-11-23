---
name: ci-lane-alias-parity
description: Fix failures of the Lane alias parity checks (Export lane alias names, Check lane aliases) on this repo's pull requests.
triggers:
  - Lane alias parity
  - Check lane aliases
  - Export lane alias names
  - lane-alias-parity
---

# ci-lane-alias-parity

## What it checks

The `lane-alias-parity` job in `.github/workflows/ci-gate.yml` calls the local
`.github/workflows/_lane-alias-parity.yml`:

- **Export lane alias names** — `tests/llm_router/lane_alias_names.py` exports the lane-class names this
  repo declares, and `scripts/generate_servable_aliases.py` renders the router names the head serves from
  the `llm-models.d/` registry; both go into an artifact.
- **Check lane aliases** — the shared `jpe-ansible/homelab-contracts` workflow runs `lane-alias-parity.py`,
  comparing the exported names with the shared lane contract (`lanes.json`) and the generated router names.

## Reproduce locally

```bash
python3 tests/llm_router/lane_alias_names.py
python3 scripts/generate_servable_aliases.py -o servable-aliases.json
```

The checker needs the shared contract at the commit pinned in `_lane-alias-parity.yml`: sparse-check out
`ansible/roles/llm_roles/files/lanes.json` and `.github/scripts/lane-alias-parity.py` from
`jpe-ansible/homelab-contracts` and run it with `--lanes`, `--router-names`, and `--names` as the workflow does.

## Cheapest fix first

CI asserts `generate_servable_aliases.py` renders deterministically; run it twice and diff if the artifact
check flags drift. Fix names at their source — the role defaults and inventory values the exporter reads, or
the `llm-models.d/` registry — never by editing the exported JSON.

## Never

- Hand-edit the exported names files or commit them into the repository.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, or any lint disable; delete, skip, or weaken
  a test or assertion; add a script; hard-code a value that belongs in a variable.

## Known causes

none recorded yet
