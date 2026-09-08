"""A role must not name the secret store an operator is expected to use.

Roles consume plain environment variables. WHICH store fills that environment
-- a secrets manager, an encrypted file, a `.env`, a CI job's secret mapping --
is the run wrapper's concern and is documented outside the code. A role that
says "add it to <store>" is wrong the moment the wrapper changes, and it is
wrong right now for anyone running the same role through a different wrapper.

The failure this prevents is a real one and it already happened: a value was
removed from one store, the role's message sent the operator to that store to
add it back, and the value they needed was sitting in a different store the
message never mentioned. The message did not merely go stale -- it actively
pointed away from the fix.

Scope note: `roles/openbao` is exempt because the store is its PAYLOAD. A role
that deploys a thing must be able to name the thing it deploys. This rule is
about where a role tells you to PUT A CREDENTIAL, not about what it installs.
"""

from __future__ import annotations

import re
from pathlib import Path

ROLES = Path(__file__).resolve().parent.parent / "roles"
SUFFIXES = {".yml", ".yaml"}
EXEMPT_PREFIXES = ("openbao",)  # the store is this role's payload, not its advice

# What is banned is telling an operator WHERE TO PUT A CREDENTIAL -- not every
# mention of a store. Several roles legitimately PUBLISH INTO a store as their
# declared job ("provide a write-capable <store> AppRole"); naming the system
# they write to is as reasonable as the openbao role naming its own payload.
# So match a store only in a SOURCING context: a preposition placing a value in
# one, an alternation offering a choice of them, or a store CLI to paste.
_STORES = r"doppler|openbao|sops|vault"
STORE = re.compile(
    r"(?:\b(?:in|into|to|from|via|under)\s+(?:the\s+)?(?P<a>" + _STORES + r")\b"
    r"|\b(?P<b>" + _STORES + r")\s*/\s*(?:" + _STORES + r")\b"
    r"|\b(?P<c>doppler)\s+(?:run|secrets|configure)\b"
    r"|\b(?P<d>sops)\s+exec-env\b)",
    re.I,
)

# Operator-facing text comes in two syntaxes and BOTH must be scanned. A
# quoted-string pattern alone silently skips every message written as a YAML
# block scalar -- which is exactly how the longest, most instruction-heavy
# messages are written, including ones that print store-specific commands for
# an operator to paste. A scan blind to a whole syntax reports clean on it.
QUOTED = re.compile(
    r"(?:mandatory\(|fail_msg:\s*|success_msg:\s*|msg:\s*)\s*['\"](?P<body>[^'\"]{0,400})['\"]",
    re.S,
)
# `fail_msg: >-` / `|` followed by an indented block, to the first dedent.
BLOCK = re.compile(
    r"^(?P<indent>\s*)(?:fail_msg|success_msg|msg):\s*[>|][-+]?\s*\n"
    r"(?P<body>(?:(?P=indent)\s+\S[^\n]*\n?)+)",
    re.M,
)


def _messages(text):
    """Yield (offset, body) for every operator-facing message, both syntaxes."""
    for pattern in (QUOTED, BLOCK):
        for match in pattern.finditer(text):
            yield match.start(), match.group("body")


def _offenders():
    for path in sorted(ROLES.rglob("*")):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        if path.relative_to(ROLES).parts[0].startswith(EXEMPT_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for offset, body in _messages(text):
            hit = STORE.search(body)
            if hit:
                line = text.count("\n", 0, offset) + 1
                yield f"{path.relative_to(ROLES.parent)}:{line} names '{hit.group(0).strip()}'"


def test_no_role_message_names_a_secret_store():
    offenders = sorted(_offenders())
    assert not offenders, (
        "These operator-facing messages name a specific secret store. State that "
        "the variable must be set in the environment; the wrapper that fills it "
        "is not this role's concern:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_can_actually_see_a_quoted_violation():
    """Anti-vacuity: a clean run must mean 'none found', not 'nothing scanned'."""
    bad = "  | mandatory('THING must be set. Add it to Doppler before deploying.') }}"
    bodies = [b for _, b in _messages(bad)]
    assert bodies, "the quoted pattern no longer matches a known-bad site"
    assert any(STORE.search(b) for b in bodies), "the store pattern no longer matches"

    good = "  | mandatory('THING must be set in the environment') }}"
    assert not any(STORE.search(b) for _, b in _messages(good)), "corrected form must read clean"


def test_the_scan_can_actually_see_a_block_scalar_violation():
    """The longest, most instruction-heavy messages are block scalars.

    A quoted-string pattern alone skips them entirely, so a scan can report
    clean on precisely the messages most likely to name a store and paste a
    store-specific command. This half exists because that gap was real: two
    such messages survived the first sweep untouched.
    """
    bad = (
        "    fail_msg: >-\n"
        "      THING must be set in Doppler before first deploy.\n"
        "      Add via: doppler secrets set THING=<value>\n"
    )
    bodies = [b for _, b in _messages(bad)]
    assert bodies, "the block-scalar pattern no longer matches a known-bad site"
    assert any(STORE.search(b) for b in bodies), "a block-scalar violation must be seen"

    good = (
        "    fail_msg: >-\n"
        "      THING must be set in the environment before first deploy.\n"
    )
    assert not any(STORE.search(b) for _, b in _messages(good)), "corrected form must read clean"


def test_scan_reaches_a_representative_number_of_messages():
    """A scan that suddenly sees almost no messages is broken, not clean."""
    seen = 0
    for path in ROLES.rglob("*"):
        if path.suffix in SUFFIXES and path.is_file():
            seen += len(list(_messages(path.read_text(encoding="utf-8", errors="ignore"))))
    assert seen >= 25, f"only {seen} operator-facing messages scanned; the scan is broken"
