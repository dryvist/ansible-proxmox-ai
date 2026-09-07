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

STORE = re.compile(r"\b(doppler|openbao|sops|vault)\b", re.I)
# Operator-facing strings only: an assertion message or a `mandatory` argument.
MESSAGE = re.compile(
    r"(?:mandatory\(|fail_msg:\s*|success_msg:\s*)\s*['\"](?P<body>[^'\"]{0,400})['\"]",
    re.S,
)


def _offenders():
    for path in sorted(ROLES.rglob("*")):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        if path.relative_to(ROLES).parts[0].startswith(EXEMPT_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in MESSAGE.finditer(text):
            hit = STORE.search(match.group("body"))
            if hit:
                line = text.count("\n", 0, match.start()) + 1
                yield f"{path.relative_to(ROLES.parent)}:{line} names '{hit.group(0)}'"


def test_no_role_message_names_a_secret_store():
    offenders = sorted(_offenders())
    assert not offenders, (
        "These operator-facing messages name a specific secret store. State that "
        "the variable must be set in the environment; the wrapper that fills it "
        "is not this role's concern:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_can_actually_see_a_violation():
    """Anti-vacuity: a clean run must mean 'none found', not 'nothing scanned'."""
    bad = "  | mandatory('THING must be set. Add it to Doppler before deploying.') }}"
    match = MESSAGE.search(bad)
    assert match is not None, "the message pattern no longer matches a known-bad site"
    assert STORE.search(match.group("body")), "the store pattern no longer matches a known-bad site"

    good = "  | mandatory('THING must be set in the environment') }}"
    clean = MESSAGE.search(good)
    assert clean is not None
    assert not STORE.search(clean.group("body")), "the corrected form must read as clean"


def test_scan_reaches_a_representative_number_of_messages():
    """A scan that suddenly sees almost no messages is broken, not clean."""
    seen = 0
    for path in ROLES.rglob("*"):
        if path.suffix in SUFFIXES and path.is_file():
            seen += len(MESSAGE.findall(path.read_text(encoding="utf-8", errors="ignore")))
    assert seen >= 25, f"only {seen} operator-facing messages scanned; the scan is broken"
