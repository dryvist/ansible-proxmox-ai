#!/usr/bin/env python3
"""Sync the Dify catalog manifest checksum pin with the pinned commit.

The role downloads agents/dify/manifest.json at dify_docker_agent_dsl_commit
and refuses to continue unless its bytes hash to
dify_docker_agent_dsl_manifest_sha256 (roles/dify_docker/vars/agent_dsls.yml).
Renovate bumps the commit on every ai-llm-prompts release but cannot recompute
the checksum, and the manifest bytes change between releases, so every bump
needs this script before the converge works again.

It fetches the manifest at the commit already pinned, stores those exact bytes
as the committed fixture the contract test compares against, rewrites the
checksum pin, and removes fixtures left over from earlier commits. Renovate
owns the commit; this script never changes it.

Usage:
    python3 scripts/update_dify_manifest_pin.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VARS_FILE = REPO_ROOT / "roles/dify_docker/vars/agent_dsls.yml"
FIXTURES_DIR = REPO_ROOT / "tests/dify_docker/fixtures"
COMMIT_LINE = re.compile(r"^(dify_docker_agent_dsl_commit:)(\s*)([0-9a-f]{40})$", re.M)
CHECKSUM_LINE = re.compile(r"^(dify_docker_agent_dsl_manifest_sha256:)(\s*)([0-9a-f]{64})$", re.M)
FIXTURE_NAME = "agent-dsl-manifest-{}.json"


def main() -> int:
    text = VARS_FILE.read_text(encoding="utf-8")
    commit = COMMIT_LINE.search(text)
    checksum = CHECKSUM_LINE.search(text)
    if commit is None:
        raise SystemExit(f"{VARS_FILE} pins no 40-hex dify_docker_agent_dsl_commit.")
    if checksum is None:
        raise SystemExit(
            f"{VARS_FILE} pins no 64-hex dify_docker_agent_dsl_manifest_sha256."
        )
    pinned = commit.group(3)
    previous = checksum.group(3)

    url = f"https://raw.githubusercontent.com/dryvist/ai-llm-prompts/{pinned}/agents/dify/manifest.json"
    with urllib.request.urlopen(url, timeout=30) as response:
        manifest = response.read()
    entries = json.loads(manifest)
    if not isinstance(entries, list) or not entries:
        raise SystemExit(f"{url} is not a non-empty JSON list; refusing to pin it.")
    missing = [key for key in ("name", "path", "sha256") if key not in entries[0]]
    if missing:
        raise SystemExit(
            f"{url} entries lack {', '.join(missing)}; the import loop reads "
            "those keys from every manifest entry, so these bytes are not a "
            "catalog this role can consume."
        )

    digest = hashlib.sha256(manifest).hexdigest()
    fixture = FIXTURES_DIR / FIXTURE_NAME.format(pinned)
    fixture.write_bytes(manifest)
    for stale in FIXTURES_DIR.glob(FIXTURE_NAME.format("*")):
        if stale.name != fixture.name:
            stale.unlink()

    VARS_FILE.write_text(
        CHECKSUM_LINE.sub(lambda match: f"{match.group(1)}{match.group(2)}{digest}", text),
        encoding="utf-8",
    )

    print(f"commit {pinned}")
    print(f"fixture {fixture.relative_to(REPO_ROOT)}")
    print(f"sha256 {previous} -> {digest}" + (" (unchanged)" if previous == digest else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
