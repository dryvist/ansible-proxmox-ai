#!/usr/bin/env python3
"""Report how many times each hermes_agent pinned-source patch matches upstream.

The tests run the role's regexps against PINNED_* snippets copied into
conftest.py. That proves a patch does what it claims, not that its anchor still
exists — so when upstream moves, the snippets keep the tests green while the
patches quietly match nothing on the guest. That is not theoretical: seven
patches were dead this way, and patches_verify.yml runs AFTER the patch tasks,
so the converge would mutate a live guest and fail only afterwards.

Run this when hermes_agent_version moves, before converging:

    python3 scripts/verify-pinned-patches.py            # the pinned version
    python3 scripts/verify-pinned-patches.py 2026.8.13  # plus another release

Every patch should report exactly 1. Investigate anything else: 0 means the
anchor is gone (re-anchor it, or retire the patch if upstream adopted the fix),
and >1 means it hits more sites than intended — unless, like the auxiliary
post-budget fallback, the duplicate site is real and deliberate.
"""

from __future__ import annotations

import io
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "hermes_agent"))

from _role_files import role_defaults, role_tasks  # noqa: E402

ROLE_ROOT = REPO_ROOT / "roles" / "hermes_agent"
TARBALL = "https://github.com/NousResearch/hermes-agent/archive/refs/tags/v{}.tar.gz"


def upstream_sources(version: str) -> dict[str, str]:
    """Return {path-within-repo: text} for one upstream release."""
    with urllib.request.urlopen(TARBALL.format(version)) as response:  # noqa: S310
        raw = response.read()
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            # Strip the leading "hermes-agent-<version>/" component.
            sources[member.name.split("/", 1)[1]] = handle.read().decode("utf-8")
    return sources


def main(versions: list[str]) -> int:
    tasks = [
        (task["name"], task["ansible.builtin.replace"])
        for task in role_tasks(ROLE_ROOT)
        if "ansible.builtin.replace" in task
    ]
    unexpected = 0
    for version in versions:
        print(f"== hermes-agent v{version}")
        sources = upstream_sources(version)
        for name, config in tasks:
            relative = str(config.get("path", "")).split("}}/")[-1]
            source = sources.get(relative)
            if source is None:
                print(f"  n/a  {name}  ({relative} not in this release)")
                continue
            count = len(re.findall(config["regexp"], source, flags=re.MULTILINE))
            print(f"  {count:>3}  {name}")
            unexpected += count != 1
    print(f"\nmatches != 1: {unexpected}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    pinned = role_defaults(ROLE_ROOT)["hermes_agent_version"]
    sys.exit(main(sys.argv[1:] or [pinned]))
