"""Behaviour of the drain-before-restart wrapper on the gateway handler.

The gateway ticks the default cron store in-process, so `state: restarted`
terminated whatever cron jobs were mid-run. Restarting on a quiet moment is not
a usable mitigation — the fleet spans several profile-scoped stores on a
5-minute tick and a single run may occupy most of an hour — so the drain has to
be mechanical: pause every store, wait for in-flight runs, restart, un-pause.

These tests render the template and run it. Nothing on the module is
substituted: claims come from real job stores on disk laid out the way the
guest lays them out, and the real `restart()` runs against a `systemctl` shim
on PATH that records what it was asked to do and what the fleet looked like at
that instant. Replacing a shipped function with a double would only prove the
double was reached — gutting `restart()` to `pass` would leave every assertion
about the restart green.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TEMPLATE = ROLE / "templates" / "hermes-cron-drain-restart.py.j2"

DEFAULTS = role_defaults(ROLE)
HERMES_USER = DEFAULTS["hermes_agent_user"]
CLAIM_TTL = int(DEFAULTS["hermes_agent_cron_fire_claim_ttl_seconds"])

# Records the restart invocation and the sentinels standing at that moment, so
# the assertions read the real subprocess call the shipped `restart()` makes.
SYSTEMCTL_SHIM = """#!/bin/sh
if [ "$1" = "$DRAIN_TEST_FAIL_ON" ]; then exit 1; fi
if [ "$1" = "restart" ]; then
  { echo "$*"; find "$DRAIN_TEST_FLEET" -name ESTOP; } > "$DRAIN_TEST_LOG"
fi
exit 0
"""


def _render(hermes_home: Path, *, drain_timeout: int) -> str:
    """Render the template the role deploys, with a test-sized drain bound."""
    source = TEMPLATE.read_text()
    return Environment(keep_trailing_newline=True).from_string(source).render(
        ansible_managed="Ansible managed",
        hermes_agent_hermes_home=str(hermes_home),
        hermes_agent_user=HERMES_USER,
        hermes_agent_cron_fire_claim_ttl_seconds=CLAIM_TTL,
        hermes_agent_cron_drain_timeout_seconds=drain_timeout,
        hermes_agent_cron_drain_poll_seconds=0,
    )


def _load(tmp_path: Path, hermes_home: Path, *, drain_timeout: int = 0):
    """Import the rendered script as a module so its functions are callable."""
    path = tmp_path / "drain_restart.py"
    path.write_text(_render(hermes_home, drain_timeout=drain_timeout))
    spec = importlib.util.spec_from_file_location("hermes_drain_restart", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _store(home: Path, *, claim_age_seconds: float | None) -> Path:
    """A cron store holding one job, laid out as the guest lays it out."""
    (home / "cron").mkdir(parents=True)
    job: dict = {"id": "j1", "name": "some-job", "fire_claim": None}
    if claim_age_seconds is not None:
        at = datetime.now(timezone.utc) - timedelta(seconds=claim_age_seconds)
        job["fire_claim"] = {"at": at.isoformat(), "by": "host:abc"}
    (home / "cron" / "jobs.json").write_text(json.dumps([job]))
    return home


@pytest.fixture
def fleet(tmp_path: Path) -> Path:
    """A default store plus two profile stores, one of them job-less."""
    home = tmp_path / "hermes"
    _store(home, claim_age_seconds=None)
    _store(home / "profiles" / "splunk-admin", claim_age_seconds=None)
    (home / "profiles" / "github-maint").mkdir(parents=True)
    return home


class Systemctl:
    """The `systemctl` the wrapper actually shells out to, on PATH."""

    def __init__(self, log: Path) -> None:
        self._log = log

    @property
    def restarted(self) -> str | None:
        """The restart argv, or None when no restart reached systemctl."""
        if not self._log.exists():
            return None
        return self._log.read_text().splitlines()[0]

    @property
    def paused_stores(self) -> list[str]:
        """Stores holding a sentinel at the moment the restart was issued."""
        if not self._log.exists():
            return []
        lines = self._log.read_text().splitlines()[1:]
        return sorted(Path(line).parent.name for line in lines if line)


@pytest.fixture
def systemctl(tmp_path: Path, fleet: Path, monkeypatch: pytest.MonkeyPatch) -> Systemctl:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "systemctl"
    shim.write_text(SYSTEMCTL_SHIM)
    shim.chmod(0o755)
    log = tmp_path / "systemctl.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DRAIN_TEST_FLEET", str(fleet))
    monkeypatch.setenv("DRAIN_TEST_LOG", str(log))
    return Systemctl(log)


def test_every_store_is_discovered_including_one_without_jobs(tmp_path, fleet) -> None:
    module = _load(tmp_path, fleet)
    assert [p.name for p in module.store_homes(fleet)] == [
        "hermes",
        "github-maint",
        "splunk-admin",
    ]


def test_a_live_claim_holds_the_restart_until_the_bound_expires(tmp_path, fleet) -> None:
    """A heartbeating runner keeps its claim younger than the lease."""
    _store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL / 2)
    module = _load(tmp_path, fleet, drain_timeout=0)

    stranded = module.wait_for_drain(module.store_homes(fleet))

    assert stranded == ["homelab-admin/some-job"]


def test_a_claim_left_by_a_dead_runner_does_not_hold_the_restart(tmp_path, fleet) -> None:
    """Past the lease the record outlives the runner, so it must not block."""
    _store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL + 1)
    module = _load(tmp_path, fleet, drain_timeout=0)

    assert module.wait_for_drain(module.store_homes(fleet)) == []


def test_a_future_dated_claim_does_not_hold_the_restart(tmp_path, fleet) -> None:
    """Clock skew must not make a claim fresh forever (upstream #60703)."""
    _store(fleet / "profiles" / "homelab-admin", claim_age_seconds=-3600)
    module = _load(tmp_path, fleet, drain_timeout=0)

    assert module.wait_for_drain(module.store_homes(fleet)) == []


def test_the_drain_ends_once_the_last_claim_ages_out(tmp_path, fleet) -> None:
    """The wait terminates on its own: ticks are quiesced, claims age out.

    A short lease rather than a stubbed claim reader, so the loop, the clock
    and the store read are the shipped ones.
    """
    _store(fleet / "profiles" / "homelab-admin", claim_age_seconds=0)
    module = _load(tmp_path, fleet)

    stranded = module.wait_for_drain(
        module.store_homes(fleet),
        ttl_seconds=0.25,
        timeout_seconds=30,
        poll_seconds=0.05,
    )

    assert stranded == []


def test_the_pause_is_lifted_even_when_the_wait_expires(tmp_path, fleet, systemctl) -> None:
    """Expiry must restart AND un-quiesce — never leave the fleet stopped."""
    _store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL / 2)
    module = _load(tmp_path, fleet, drain_timeout=0)

    assert module.main() == 0
    assert systemctl.restarted == "restart hermes-gateway"
    assert not list(fleet.rglob("ESTOP"))


def test_the_pause_is_lifted_when_the_restart_fails(tmp_path, fleet, systemctl,
                                                    monkeypatch) -> None:
    """The `finally` covers the restart itself, not just the wait."""
    monkeypatch.setenv("DRAIN_TEST_FAIL_ON", "daemon-reload")
    module = _load(tmp_path, fleet)

    with pytest.raises(subprocess.CalledProcessError):
        module.main()

    assert systemctl.restarted is None
    assert not list(fleet.rglob("ESTOP"))


def test_a_store_paused_by_an_operator_stays_paused(tmp_path, fleet, systemctl) -> None:
    """An existing pause is someone else's; the drain must not lift it."""
    (fleet / "profiles" / "splunk-admin" / "ESTOP").write_text("{}")
    module = _load(tmp_path, fleet)

    assert module.main() == 0
    assert systemctl.restarted == "restart hermes-gateway"
    assert [p.parent.name for p in fleet.rglob("ESTOP")] == ["splunk-admin"]


def test_every_store_is_quiesced_before_the_restart(tmp_path, fleet, systemctl) -> None:
    """The restart must not run against a store still admitting new fires."""
    module = _load(tmp_path, fleet)

    assert module.main() == 0
    assert systemctl.paused_stores == ["github-maint", "hermes", "splunk-admin"]


def test_the_handler_runs_the_wrapper_rather_than_restarting_the_unit() -> None:
    """The whole fix is worthless if a handler still restarts the unit raw."""
    handlers = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
    gateway = next(h for h in handlers if h["name"] == "Restart hermes-gateway")

    assert "ansible.builtin.systemd" not in gateway
    assert gateway["ansible.builtin.command"] == (
        "{{ hermes_agent_cron_drain_restart_script }}"
    )


def test_the_drain_bound_tracks_the_per_run_wall_clock_ceiling() -> None:
    """A bound shorter than a permitted run would cut short legitimate work."""
    assert DEFAULTS["hermes_agent_cron_drain_timeout_seconds"] == (
        "{{ hermes_agent_cron_wall_timeout_seconds }}"
    )
