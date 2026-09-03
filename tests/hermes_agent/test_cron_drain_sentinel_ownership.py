"""Whose pause is it, and may this run clear it?

A hard kill — SIGKILL, an OOM kill, control-node power loss — reaches no
`finally`, so the sentinel survives with nothing to release it. Left as-is that
orphan is indistinguishable from an operator's pause, the next converge
preserves it, and the fleet stays quiesced forever with no failure and no
alarm, because a paused fleet looks exactly like a quiet one. It would also
leave a latch automation can set and only a human can clear.

So ownership is explicit: a sentinel carries an owner marker and a timestamp,
and a run clears only one it can positively identify as its own and older than
any live drain could be. Everything else is left alone and said out loud.

Drain mechanics live in test_cron_drain_before_restart.py; the shared harness
is _drain_shared.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from _drain_shared import (  # noqa: F401 - fleet/systemctl are fixtures
    converge_sentinel,
    fleet,
    load,
    systemctl,
)


def test_a_store_paused_by_an_operator_stays_paused(tmp_path, fleet, systemctl) -> None:
    """An existing pause is someone else's; the drain must not lift it."""
    (fleet / "profiles" / "splunk-admin" / "ESTOP").write_text("{}")
    module = load(tmp_path, fleet)

    assert module.main() == 0
    assert systemctl.restarted == "restart hermes-gateway"
    assert [p.parent.name for p in fleet.rglob("ESTOP")] == ["splunk-admin"]


def test_a_bare_touch_pause_survives(tmp_path, fleet, systemctl) -> None:
    """`touch $HERMES_HOME/ESTOP` is the documented manual pause: zero bytes."""
    module = load(tmp_path, fleet)
    (fleet / "profiles" / "splunk-admin" / "ESTOP").touch()

    assert module.main() == 0
    assert [p.parent.name for p in fleet.rglob("ESTOP")] == ["splunk-admin"]


def test_a_pause_orphaned_by_a_hard_kill_is_reclaimed_and_released(
    tmp_path, fleet, systemctl
) -> None:
    """A converge killed with SIGKILL reaches no `finally` and leaves one."""
    module = load(tmp_path, fleet)
    converge_sentinel(
        module, fleet / "profiles" / "splunk-admin",
        age_seconds=module.ORPHAN_AFTER_SECONDS + 4242,
    )

    assert module.main() == 0
    assert systemctl.restarted == "restart hermes-gateway"
    assert not list(fleet.rglob("ESTOP"))


def test_clearing_an_orphan_says_so_with_its_age(tmp_path, fleet, systemctl, capsys) -> None:
    """A guard that acts silently is how damage goes unnoticed."""
    module = load(tmp_path, fleet)
    age = module.ORPHAN_AFTER_SECONDS + 4242
    converge_sentinel(module, fleet / "profiles" / "splunk-admin", age_seconds=age)

    assert module.main() == 0

    cleared = [
        line for line in capsys.readouterr().out.splitlines()
        if "splunk-admin" in line and "clearing" in line
    ]
    assert len(cleared) == 1, "clearing a pause must be announced exactly once"
    assert f"{age:.0f}s ago" in cleared[0], "the age of what was cleared must be stated"


def test_a_sibling_converge_still_draining_is_not_robbed(tmp_path, fleet, systemctl) -> None:
    """Owned but young means a converge is mid-drain, not that it died."""
    module = load(tmp_path, fleet)
    converge_sentinel(module, fleet / "profiles" / "splunk-admin", age_seconds=5)

    assert module.main() == 0
    assert [p.parent.name for p in fleet.rglob("ESTOP")] == ["splunk-admin"]


def test_an_unreadable_sentinel_is_treated_as_someone_elses(tmp_path, fleet, systemctl) -> None:
    """Reclaim only what is positively ours — when in doubt, leave the pause."""
    module = load(tmp_path, fleet)
    (fleet / "profiles" / "splunk-admin" / "ESTOP").write_text("not json {")

    assert module.main() == 0
    assert [p.parent.name for p in fleet.rglob("ESTOP")] == ["splunk-admin"]


def test_an_old_pause_without_the_owner_marker_is_never_reclaimed(
    tmp_path, fleet, systemctl
) -> None:
    """Age alone is not ownership: an operator pause may be arbitrarily old."""
    module = load(tmp_path, fleet)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    (fleet / "profiles" / "splunk-admin" / "ESTOP").write_text(
        json.dumps({"engaged_at": old.isoformat(), "reason": "operator"})
    )

    assert module.main() == 0
    assert [p.parent.name for p in fleet.rglob("ESTOP")] == ["splunk-admin"]


def test_an_operator_clearing_sentinels_by_hand_mid_run_does_not_break_the_release(
    tmp_path, fleet, systemctl, monkeypatch
) -> None:
    """The panicking-human case: sentinels vanish before the release runs.

    `unlink` on a file someone already removed raises FileNotFoundError, an
    OSError — which the release swallows — so the wrapper still exits cleanly
    and reports the restart. Clearing by hand does not crash the wrapper; it
    just un-pauses the fleet earlier than the design intends.
    """
    monkeypatch.setenv("DRAIN_TEST_RM_SENTINELS", "1")
    module = load(tmp_path, fleet)

    assert module.main() == 0
    assert systemctl.restarted == "restart hermes-gateway"
    assert not list(fleet.rglob("ESTOP"))
