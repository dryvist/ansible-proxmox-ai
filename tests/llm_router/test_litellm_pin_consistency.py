"""The litellm[proxy] version lives only in its Renovate-managed pin
(defaults/main/00-identity.yml). Docs name the pin file, never a version
number, so a Renovate bump cannot leave them citing a release nobody runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PIN_FILE = REPO_ROOT / "roles" / "llm_router" / "files" / "requirements.in"
LOCK_FILE = REPO_ROOT / "roles" / "llm_router" / "files" / "requirements.txt"
IDENTITY_FILE = REPO_ROOT / "roles" / "llm_router" / "defaults" / "main" / "00-identity.yml"
INSTALL_TASK = REPO_ROOT / "roles" / "llm_router" / "tasks" / "install-runtime.yml"
RENOVATE_FILE = REPO_ROOT / "renovate.json"

# Docs that describe LiteLLM release behavior.
DOCS = [
    REPO_ROOT / "roles" / "llm_router" / "README.md",
    REPO_ROOT / "docs" / "LLM_ROUTER_SETTINGS_SEED_MODE.md",
]

VERSION_RE = re.compile(r"\b\d+\.\d+\.(?:\d+|x)\b|\bv\d+[.-]\d+[.-]\d+\b")


def test_pin_is_declared():
    source = PIN_FILE.read_text()
    pin = re.search(r"(?m)^litellm\[proxy\]==(?P<version>\d+\.\d+\.\d+)$", source)
    assert pin, f"{PIN_FILE} does not declare a litellm[proxy]==X.Y.Z pin"
    assert "# renovate: datasource=pypi depName=litellm" in source
    assert "requirements.in" in IDENTITY_FILE.read_text()

    lock_pin = re.search(
        r"(?m)^litellm(?:\[proxy\])?==(?P<version>\d+\.\d+\.\d+)",
        LOCK_FILE.read_text(),
    )
    assert lock_pin and lock_pin.group("version") == pin.group("version"), (
        "The compiled lock must contain the Renovate-managed LiteLLM source pin"
    )


def test_every_compiled_requirement_has_a_hash():
    lock = LOCK_FILE.read_text()
    package_line = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[^]\n]+\])?==")
    lines = lock.splitlines()
    blocks = []
    for index, line in enumerate(lines):
        if not package_line.match(line):
            continue
        block = [line]
        for following in lines[index + 1 :]:
            if package_line.match(following):
                break
            block.append(following)
        blocks.append((line, "\n".join(block)))

    assert blocks, f"{LOCK_FILE} has no compiled package entries"
    unhashed = [line for line, block in blocks if "--hash=sha256:" not in block]
    assert not unhashed, "Compiled requirements without hashes:\n" + "\n".join(unhashed)


def test_installer_uses_hash_enforcement():
    task = INSTALL_TASK.read_text()
    assert 'requirements: "{{ llm_router_home }}/requirements.txt"' in task
    assert "extra_args: --require-hashes" in task


def test_renovate_compiles_the_lock_with_the_pypi_source():
    config = json.loads(RENOVATE_FILE.read_text())
    lock_pattern = "/^roles\\/llm_router\\/files\\/requirements\\.txt$/"
    assert lock_pattern in config["pip-compile"]["managerFilePatterns"]
    assert any(
        rule.get("matchManagers") == ["pip_requirements"]
        and rule.get("matchFileNames") == ["roles/llm_router/files/requirements.txt"]
        and rule.get("enabled") is False
        for rule in config["packageRules"]
    ), "The generated lock must be handled only by the pip-compile manager"


def test_docs_cite_no_version_number():
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
        for path in DOCS
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if VERSION_RE.search(line)
    ]
    assert not offenders, (
        "Version numbers belong in Renovate-managed pins; name the pin file instead:\n"
        + "\n".join(offenders)
    )
