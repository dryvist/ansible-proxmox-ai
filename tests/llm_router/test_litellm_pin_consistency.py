"""The litellm[proxy] version lives only in its Renovate-managed pin
(defaults/main/00-identity.yml). Docs name the pin file, never a version
number, so a Renovate bump cannot leave them citing a release nobody runs.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PIN_FILE = REPO_ROOT / "roles" / "llm_router" / "defaults" / "main" / "00-identity.yml"

# Docs that describe LiteLLM release behavior.
DOCS = [
    REPO_ROOT / "roles" / "llm_router" / "README.md",
    REPO_ROOT / "docs" / "LLM_ROUTER_SETTINGS_SEED_MODE.md",
]

VERSION_RE = re.compile(r"\b\d+\.\d+\.(?:\d+|x)\b|\bv\d+[.-]\d+[.-]\d+\b")


def test_pin_is_declared():
    assert re.search(r"litellm\[proxy\]==\d+\.\d+\.\d+", PIN_FILE.read_text()), (
        f"{PIN_FILE} does not declare a litellm[proxy]==X.Y.Z pin"
    )


def test_docs_cite_no_version_number():
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
        for path in DOCS
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if VERSION_RE.search(line)
    ]
    assert not offenders, (
        "Version numbers belong only in Renovate-managed pins; name the pin file instead:\n"
        + "\n".join(offenders)
    )
