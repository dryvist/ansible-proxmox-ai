"""Restart-loop bounds on the long-running Hermes units.

hermes-gateway, hermes-dashboard and hermes-vikunja-bridge are all
Restart=always. With no start-rate limit, a config-broken unit restarted every
5-10s indefinitely: CPU burned, state reported as "activating", nobody alerted.

The bound alone would be strictly worse — systemd gives up and the unit sits
dead, equally silently. So the contract these tests pin is the PAIR: a bound,
plus an OnFailure= that says the bound was hit.
"""

from __future__ import annotations

import re
from pathlib import Path

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TEMPLATES = ROLE / "templates"
DEFAULTS = (ROLE / "defaults" / "main.yml").read_text()
TASKS = (ROLE / "tasks" / "main.yml").read_text()

LONG_RUNNING = [
    "hermes-gateway.service.j2",
    "hermes-dashboard.service.j2",
    "hermes-vikunja-bridge.service.j2",
]


def _unit(name: str) -> str:
    return (TEMPLATES / name).read_text()


def _int_default(name: str) -> int:
    """Read one integer role default, failing loudly if the key is gone."""
    match = re.search(rf"^{name}:\s*(\d+)", DEFAULTS, re.M)
    assert match is not None, f"could not find {name} — renamed or removed?"
    return int(match.group(1))


def _section(text: str, name: str) -> str:
    """Return the body of one systemd section.

    Line-anchored on purpose: a section header is a line that IS "[Name]". A
    naive substring split matches the same text inside a comment — these units
    legitimately mention "[Service]" in prose explaining where StartLimit* must
    not go, which silently mis-sliced the section and inverted this test.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == f"[{name}]"]
    assert starts, f"no [{name}] section header"
    out: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if re.fullmatch(r"\[[A-Za-z]+\]", line.strip()):
            break
        out.append(line)
    return "\n".join(out)


def test_every_restart_always_unit_is_bounded() -> None:
    for name in LONG_RUNNING:
        unit = _unit(name)
        assert "Restart=always" in unit, f"{name}: fixture assumes Restart=always"
        assert "StartLimitBurst=" in unit, f"{name}: unbounded restart loop"
        assert "StartLimitIntervalSec=" in unit, f"{name}: unbounded restart loop"


def test_start_limits_live_in_the_unit_section() -> None:
    """systemd 229+ moved StartLimit* from [Service] to [Unit].

    Left in [Service] they are ignored with only a log warning — the unit would
    look bounded in review while looping unbounded in production. This is the
    failure mode most likely to be reintroduced, because the neighbouring
    Restart=/RestartSec= keys DO belong in [Service].
    """
    for name in LONG_RUNNING:
        unit = _unit(name)
        service = _section(unit, "Service")
        assert "StartLimit" not in service, (
            f"{name}: StartLimit* in [Service] is ignored by systemd 229+; move it to [Unit]"
        )
        assert "StartLimitBurst=" in _section(unit, "Unit"), f"{name}: StartLimit* must be in [Unit]"


def test_hitting_the_bound_pages_instead_of_dying_quietly() -> None:
    """A bound without an alert trades a silent loop for a silent outage."""
    for name in LONG_RUNNING:
        assert "OnFailure=hermes-unit-alert@%n.service" in _section(_unit(name), "Unit"), (
            f"{name}: bounded restarts must page when the bound is hit"
        )


def test_the_alert_unit_and_script_are_actually_deployed() -> None:
    """An OnFailure= naming a unit that was never installed is a silent no-op."""
    assert "hermes-unit-alert.sh.j2" in TASKS
    assert "hermes-unit-alert@.service.j2" in TASKS
    assert (TEMPLATES / "hermes-unit-alert@.service.j2").exists()
    assert (TEMPLATES / "hermes-unit-alert.sh.j2").exists()


def test_the_alert_deploy_is_not_gated_on_the_brain_watchdog() -> None:
    """The long-running units exist whether or not the brain watchdog is
    enabled, so gating the alert unit on it would leave their OnFailure=
    pointing at something never installed.
    """
    block = TASKS.split("Deploy the Hermes per-unit failure alert script", 1)[1]
    block = block.split("- name:", 1)[0]
    assert "when:" not in block, "the per-unit alert script must deploy unconditionally"


def test_the_bound_tolerates_an_ordinary_flaky_start() -> None:
    """Too tight a bound turns a slow dependency into a paged outage."""
    burst = _int_default("hermes_agent_unit_restart_burst")
    window = _int_default("hermes_agent_unit_restart_interval_sec")
    assert burst >= 3, "fewer than 3 starts pages on ordinary dependency flap"
    assert window >= 60, "a sub-minute window makes the burst count meaningless"
