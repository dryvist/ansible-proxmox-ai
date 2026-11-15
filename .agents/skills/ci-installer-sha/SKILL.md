---
name: ci-installer-sha
description: Fix failures of the Installer checksums check (pinned installer and release digest mismatches) on this repo's pull requests.
triggers:
  - Installer checksums
  - installer-sha
  - check-installer-sha
---

## What it checks

The `installer-sha` job ("Installer checksums") in `.github/workflows/ci-gate.yml` runs `.github/scripts/check-installer-sha.sh`
on every pull request, recomputing each checksum-verified pin against the role defaults:

- the hermes-agent installer script sha256 and the hindsight plugin archive sha256 in `roles/hermes_agent/defaults/main/`;
- the pinned llama.cpp release tag and its declared release-asset digests in `roles/llamacpp_release/defaults/main/00-release.yml`.

Renovate bumps versions but cannot compute checksums, so a version bump beside a stale sha is the expected
failure.

## Reproduce locally

```bash
.github/scripts/check-installer-sha.sh
```

Set `GITHUB_TOKEN` to avoid API rate limiting. The script needs `curl`, `jq`, and `shasum`.

## Cheapest fix first

The script has a built-in autofix that recomputes and rewrites the stale digest in place:

```bash
.github/scripts/check-installer-sha.sh --fix
```

Review the diff afterwards: only the pinned digest lines may change, never the versions.

## Never

- Hand-edit a digest to a guessed value instead of letting `--fix` compute it.
- Delete a pin row, comment out a check, or bump a version without its matching digest.
- Add `# noqa`, `# nosec`, `skip_ansible_lint`, `tflint-ignore`, or any lint disable; delete, skip, or weaken
  a test or assertion; add a script; hard-code a value that belongs in a variable.

## Known causes

none recorded yet
