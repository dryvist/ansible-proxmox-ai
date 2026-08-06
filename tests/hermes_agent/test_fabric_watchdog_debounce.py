"""fabric_watchdog debounce contract.

The role alerts on up/down transitions for the MCP fabric and LLM front door.
It originally committed an edge on a SINGLE differing probe, which — with an 8s
deadline against Traefik-pooled endpoints — made one slow response an outage and
its recovery two minutes later a second message. A 13-day audit found 86
messages in the work channel, several recoveries with no matching DOWN.

These tests pin the hysteresis that fixed it, and pin that it stays honest: a
debounce is only legitimate while it still commits a SUSTAINED edge.

Lives under tests/hermes_agent/ because fabric_watchdog runs on the Hermes guest
and shares that role's Slack EnvironmentFile (see test_alert_routing.py).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "roles" / "fabric_watchdog"
SCRIPT = (ROLE / "templates" / "fabric-watchdog.sh.j2").read_text()
DEFAULTS = (ROLE / "defaults" / "main.yml").read_text()


def _int_var(name: str) -> int:
    match = re.search(rf"^{name}:\s*(\d+)", DEFAULTS, re.M)
    assert match is not None, f"could not find {name} — renamed or templated away?"
    return int(match.group(1))


def test_an_edge_requires_consecutive_agreeing_probes() -> None:
    """One blip must not be able to speak."""
    assert 'if [[ "${state}" == "${last_obs}" ]]; then streak=$(( streak + 1 )); else streak=1; fi' in SCRIPT
    assert "if (( streak >= needed )); then" in SCRIPT
    assert 'if [[ "${state}" == "down" ]]; then needed="${DOWN_AFTER}"; else needed="${UP_AFTER}"; fi' in SCRIPT


def test_a_flapping_endpoint_cannot_accumulate_its_way_to_an_edge() -> None:
    """The streak counts consecutive AGREEING observations, so an endpoint that
    alternates up/down resets to 1 every probe and never reaches the threshold.
    Counting total disagreements instead would let a flap page eventually.
    """
    assert "else streak=1; fi" in SCRIPT, "a flipped observation must reset the streak to 1, not decrement it"


def test_the_committed_state_only_advances_on_an_alerted_edge() -> None:
    """The .state file must be written INSIDE the threshold branch. Writing it on
    every probe would mark the endpoint changed without alerting, so the real
    edge — when it finally sustains — would look like no change at all and stay
    silent forever.
    """
    threshold_branch = SCRIPT.split("if (( streak >= needed )); then", 1)[1]
    assert '.state"' in threshold_branch, "committed state must only advance once an edge is alerted"


def test_debounce_is_never_reduced_to_the_undebounced_behaviour() -> None:
    """down_after/up_after of 1 restores exactly the noisy original. Guard the
    values, not just the code shape — otherwise the fix can be reverted in a
    defaults tweak with every test still green.
    """
    down_after = _int_var("fabric_watchdog_down_after")
    up_after = _int_var("fabric_watchdog_up_after")
    assert down_after >= 2, "down_after=1 is the un-debounced behaviour this role was fixed for"
    assert up_after >= 2, "up_after=1 lets a single lucky probe declare recovery"


def test_debounce_still_pages_within_a_useful_window() -> None:
    """The role exists for minutes-level detection of a fabric outage, so the
    debounce must not push detection into tens of minutes. Silence bought by an
    unusable detection delay is not a fix.
    """
    interval = re.search(r'^fabric_watchdog_interval:\s*"(\d+)min"', DEFAULTS, re.M)
    assert interval is not None
    minutes_to_page = int(interval.group(1)) * _int_var("fabric_watchdog_down_after")
    assert minutes_to_page <= 10, (
        f"{minutes_to_page} min to detect a fabric outage is too slow for a "
        "minutes-level watchdog; lower down_after or the interval"
    )
