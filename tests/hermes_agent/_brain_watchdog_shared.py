"""Shared fixtures for the Hermes brain-watchdog safety-contract self-checks.

WHY THIS EXISTS. `hermes_agent_brain_watchdog_enabled` was flipped to true
2026-08-01 (a Caddy llm-gate crash that took the primary AND fallback serving
legs down at once, twice in one night, with the seeded cron fleet firing into
a dead brain the whole time — exactly the gap this watchdog closes). Before
that happened, the watchdog's decision logic needed a contract, because it
does not merely alert: it `pause`/`resume`s the role-seeded cron fleet. A
watchdog that pauses the whole fleet on a single unlucky probe would silently
stop all Hermes work, and the symptom — nothing running — looks identical to
an idle board.

These are static assertions against the template text, matching the style used
by test_alert_routing.py and test_goal_mode_contract.py. They pin the
PROPERTIES that make enabling safe, not the exact numbers — the numbers are
role variables and an operator may tune them.
"""

import re
from pathlib import Path

from _role_files import role_defaults_text

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
REPO_ROOT = ROLE.parents[1]
WATCHDOG = (ROLE / "templates" / "hermes-brain-watchdog.sh.j2").read_text()
DEFAULTS = role_defaults_text(ROLE)
ROUTER_DEFAULTS = role_defaults_text(REPO_ROOT / "roles" / "llm_router")
ALL_VARS = (REPO_ROOT / "inventory" / "group_vars" / "all.yml").read_text()


def _int_var(pattern: str, haystack: str, what: str) -> int:
    """Extract one integer setting, failing loudly when the key is gone.

    A bare `re.search(...).group(1)` raises AttributeError on a renamed or
    removed key, which reads as a broken test rather than a broken contract.
    """
    match = re.search(pattern, haystack, re.M)
    assert match is not None, f"could not find {what} — was it renamed or templated away?"
    return int(match.group(1))
