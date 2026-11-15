---
name: ci-installer-sha
description: Fix failures in the Installer checksums check that pairs version pins with sha256 digests.
triggers:
  - Installer checksums
  - check-installer-sha
---

# Installer checksums

## What it checks

`ci-gate.yml` job `installer-sha` ("Installer checksums") runs
`.github/scripts/check-installer-sha.sh` on every PR. It downloads each pinned installer or release
asset in `roles/hermes_agent/` and `roles/llamacpp_release/` and asserts its sha256 matches the
paired pin, so a Renovate version bump with a stale checksum fails at PR time, not at converge.

## Reproduce locally

```bash
.github/scripts/check-installer-sha.sh
```

## Cheapest fix first

The script ships its own autofix — use it before any manual edit:

```bash
.github/scripts/check-installer-sha.sh --fix
```

## Never

- Hand-edit a sha256 without computing it via `--fix`.
- Delete a `PINS`/`RELEASE_PINS` row or drop the guard to make the check pass.
- Delete, skip or weaken a test or assertion.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, a lint disable, a Terraform
  `data "external"`, or a script; hard-code a value that belongs in a variable.

## Known causes

A Renovate bump changed a version pin without updating its paired sha256; `--fix` recomputes it.
