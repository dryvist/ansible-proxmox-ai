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

Every patch should report its declared expected count (one unless the task
explicitly declares otherwise). Patches are applied to the in-memory source in
role order after counting, because later patches can intentionally anchor on an
earlier patch's output just as they do during a real converge.
"""

from __future__ import annotations

import io
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

from jinja2 import DebugUndefined, Environment, UndefinedError

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
        task
        for task in role_tasks(ROLE_ROOT)
        if "ansible.builtin.replace" in task
    ]
    environment = Environment(autoescape=False, undefined=DebugUndefined)
    unexpected = 0
    for version in versions:
        print(f"== hermes-agent v{version}")
        sources = upstream_sources(version)
        for task in tasks:
            name = task["name"]
            config = task["ansible.builtin.replace"]
            expected = int(
                task.get("vars", {}).get(
                    "hermes_agent_pinned_patch_expected_matches", 1
                )
            )
            relative = str(config.get("path", "")).split("}}/")[-1]
            source = sources.get(relative)
            if source is None:
                print(f"  n/a  {name}  ({relative} not in this release)")
                continue
            count = len(re.findall(config["regexp"], source, flags=re.MULTILINE))
            print(f"  {count:>3}/{expected:<3}  {name}")
            unexpected += count != expected
            if count:
                try:
                    replacement = environment.from_string(config["replace"]).render(
                        **task.get("vars", {})
                    )
                except UndefinedError:
                    # Matching is already proven. Preserve unresolved role vars
                    # literally when a later patch does not depend on them.
                    replacement = config["replace"]
                sources[relative] = re.sub(
                    config["regexp"], replacement, source, flags=re.MULTILINE
                )
    print(f"\nunexpected match counts: {unexpected}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    pinned = role_defaults(ROLE_ROOT)["hermes_agent_version"]
    sys.exit(main(sys.argv[1:] or [pinned]))
