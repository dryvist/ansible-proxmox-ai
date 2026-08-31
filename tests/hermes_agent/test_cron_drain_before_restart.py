"""Drain mechanics: what is in flight, how long to wait, when to restart.

The gateway ticks the default cron store in-process, so `state: restarted`
terminated whatever cron jobs were mid-run. Restarting on a quiet moment is not
a usable mitigation — the fleet spans several profile-scoped stores on a
5-minute tick and a single run may occupy most of an hour — so the drain has to
be mechanical: pause every store, wait for in-flight runs, restart, un-pause.

Ownership of the pauses is the other half and lives in
test_cron_drain_sentinel_ownership.py; the shared harness is _drain_shared.py.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from _drain_shared import (  # noqa: F401 - fleet/systemctl are fixtures
    CLAIM_TTL,
    ROLE,
    Systemctl,
    fleet,
    load,
    store,
    systemctl,
)


def test_every_store_is_discovered_including_one_without_jobs(tmp_path, fleet) -> None:
    module = load(tmp_path, fleet)
    assert [p.name for p in module.store_homes(fleet)] == [
        "hermes",
        "github-maint",
        "splunk-admin",
    ]


def test_a_live_claim_holds_the_restart_until_the_bound_expires(tmp_path, fleet) -> None:
    """A heartbeating runner keeps its claim younger than the lease."""
    store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL / 2)
    module = load(tmp_path, fleet, drain_timeout=0)

    assert module.wait_for_drain(module.store_homes(fleet)) == [
        "homelab-admin/some-job"
    ]


def test_a_claim_left_by_a_dead_runner_does_not_hold_the_restart(tmp_path, fleet) -> None:
    """Past the lease the record outlives the runner, so it must not block."""
    store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL + 1)
    module = load(tmp_path, fleet, drain_timeout=0)

    assert module.wait_for_drain(module.store_homes(fleet)) == []


def test_a_future_dated_claim_does_not_hold_the_restart(tmp_path, fleet) -> None:
    """Clock skew must not make a claim fresh forever (upstream #60703)."""
    store(fleet / "profiles" / "homelab-admin", claim_age_seconds=-3600)
    module = load(tmp_path, fleet, drain_timeout=0)

    assert module.wait_for_drain(module.store_homes(fleet)) == []


def test_the_drain_ends_once_the_last_claim_ages_out(tmp_path, fleet) -> None:
    """The wait terminates on its own: ticks are quiesced, claims age out.

    A short lease rather than a stubbed claim reader, so the loop, the clock
    and the store read are the shipped ones.
    """
    store(fleet / "profiles" / "homelab-admin", claim_age_seconds=0)
    module = load(tmp_path, fleet)

    assert module.wait_for_drain(
        module.store_homes(fleet),
        ttl_seconds=0.25,
        timeout_seconds=30,
        poll_seconds=0.05,
    ) == []


def test_the_pause_is_lifted_even_when_the_wait_expires(tmp_path, fleet, systemctl) -> None:
    """Expiry must restart AND un-quiesce — never leave the fleet stopped."""
    store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL / 2)
    module = load(tmp_path, fleet, drain_timeout=0)

    assert module.main() == 0
    assert systemctl.restarted == "restart hermes-gateway"
    assert not list(fleet.rglob("ESTOP"))


def test_the_pause_is_lifted_when_the_restart_fails(tmp_path, fleet, systemctl,
                                                    monkeypatch) -> None:
    """The `finally` covers the restart itself, not just the wait."""
    monkeypatch.setenv("DRAIN_TEST_FAIL_ON", "daemon-reload")
    module = load(tmp_path, fleet)

    with pytest.raises(subprocess.CalledProcessError):
        module.main()

    assert systemctl.restarted is None
    assert not list(fleet.rglob("ESTOP"))


def test_every_store_is_quiesced_before_the_restart(tmp_path, fleet, systemctl) -> None:
    """The restart must not run against a store still admitting new fires."""
    module = load(tmp_path, fleet)

    assert module.main() == 0
    assert systemctl.paused_stores == ["github-maint", "hermes", "splunk-admin"]


def test_a_clean_drain_reports_what_it_actually_scanned(
    tmp_path, fleet, systemctl, capsys
) -> None:
    """"No in-flight runs" must not read the same as "looked at nothing".

    A drain scanning zero stores and a drain scanning the whole fleet produced
    the identical clean line, so a blind guard was indistinguishable from a
    working one.
    """
    store(fleet / "profiles" / "homelab-admin", claim_age_seconds=None)
    module = load(tmp_path, fleet)

    assert module.main() == 0

    clean = [ln for ln in capsys.readouterr().out.splitlines() if "no in-flight" in ln]
    assert len(clean) == 1
    assert "4 store(s)" in clean[0]
    assert "3 job(s)" in clean[0], "github-maint has no store; the other three hold one"


def test_a_fleet_with_no_readable_stores_says_zero_rather_than_nothing(
    tmp_path, systemctl, capsys
) -> None:
    """The blind case must be visible, not silently identical to a clean one."""
    blind = tmp_path / "blind"
    blind.mkdir()
    module = load(tmp_path, blind)

    assert module.main() == 0
    assert "0 job(s)" in capsys.readouterr().out


def test_the_handler_runs_the_wrapper_rather_than_restarting_the_unit() -> None:
    """The whole fix is worthless if a handler still restarts the unit raw."""
    handlers = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
    gateway = next(h for h in handlers if h["name"] == "Restart hermes-gateway")

    assert "ansible.builtin.systemd" not in gateway
    assert gateway["ansible.builtin.command"] == (
        "{{ hermes_agent_cron_drain_restart_script }}"
    )


def test_the_drain_decisions_reach_the_converge_output() -> None:
    """`command` captures stdout, so without this the decisions are discarded.

    The wrapper logs a pause decision per store, its wait, and its release
    count — and at default verbosity a converge showed only "changed". A guard
    nobody can see deciding is the failure this whole change exists to prevent.
    """
    handlers = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
    gateway = next(h for h in handlers if h["name"] == "Restart hermes-gateway")

    echo = next(
        h for h in handlers
        if h.get("listen") == "Restart hermes-gateway"
        and "ansible.builtin.debug" in h
    )
    assert gateway["register"] in echo["ansible.builtin.debug"]["var"]


def test_the_drain_bound_tracks_the_per_run_wall_clock_ceiling() -> None:
    """A bound shorter than a permitted run would cut short legitimate work."""
    from _drain_shared import DEFAULTS

    assert DEFAULTS["hermes_agent_cron_drain_timeout_seconds"] == (
        "{{ hermes_agent_cron_wall_timeout_seconds }}"
    )
