"""The litellm[proxy] version pin has exactly one base variable
(defaults/main/00-identity.yml) — everything else that cites "the litellm
version this was verified against" must name the SAME version, or it is
describing facts about a release nobody runs. Catches the doc going stale on
the next version bump, the way this PR's own bump would have without it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PIN_FILE = REPO_ROOT / "roles" / "llm_router" / "defaults" / "main" / "00-identity.yml"
PIN_RE = re.compile(r'litellm\[proxy\]==(\d+\.\d+\.\d+)')

# Files whose own "verified against litellm==X" framing must track the pin.
# Not every "litellm 1.98.0" source-citation comment in the repo belongs here —
# only the docs whose header/closing line names a specific verified version.
CITING_FILES = [
    REPO_ROOT / "roles" / "llm_router" / "README.md",
    REPO_ROOT / "docs" / "LLM_ROUTER_SETTINGS_SEED_MODE.md",
]


def _pinned_version() -> str:
    match = PIN_RE.search(PIN_FILE.read_text())
    assert match, f"{PIN_FILE} does not declare a litellm[proxy]==X.Y.Z pin"
    return match.group(1)


def test_citing_docs_name_the_current_pin():
    pinned = _pinned_version()
    offenders = []
    for path in CITING_FILES:
        text = path.read_text()
        versions = set(re.findall(r"litellm==(\d+\.\d+\.\d+)", text))
        if not versions:
            offenders.append(f"{path}: no litellm==X.Y.Z citation found")
        elif versions != {pinned}:
            offenders.append(f"{path}: cites {sorted(versions)}, pin is {pinned}")
    assert not offenders, (
        f"litellm[proxy] is pinned to {pinned} in {PIN_FILE}, but:\n"
        + "\n".join(offenders)
        + "\nUpdate the citing doc's version (and re-verify its facts) on every pin bump."
    )
