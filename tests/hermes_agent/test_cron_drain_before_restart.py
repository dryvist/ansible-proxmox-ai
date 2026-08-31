"""Behaviour of the drain-before-restart wrapper on the gateway handler.

The gateway ticks the default cron store in-process, so `state: restarted`
terminated whatever cron jobs were mid-run. Restarting on a quiet moment is not
a usable mitigation — the fleet spans several profile-scoped stores on a
5-minute tick and a single run may occupy most of an hour — so the drain has to
be mechanical: pause every store, wait for in-flight runs, restart, un-pause.

These tests render the template and exercise it, rather than asserting on its
text, because the three properties that make it safe are behavioural: a live
claim must hold the restart, a dead runner's leftover claim must not, and the
pause must be lifted even when the wait expires.
"""

from __future__ import annotations

import importlib.util
import json
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
    """Create a cron store holding one job, optionally with a fire claim."""
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
    """The wait terminates on its own: ticks are quiesced, claims expire."""
    _store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL / 2)
    module = _load(tmp_path, fleet, drain_timeout=CLAIM_TTL * 2)
    homes = module.store_homes(fleet)
    seen: list[Path] = []

    def _claims_that_clear_after_one_sweep(home, _now, _ttl):
        seen.append(home)
        return ["homelab-admin/some-job"] if len(seen) <= len(homes) else []

    module.claimed_jobs = _claims_that_clear_after_one_sweep

    assert module.wait_for_drain(homes) == []


def test_the_pause_is_lifted_even_when_the_wait_expires(tmp_path, fleet) -> None:
    """Expiry must restart AND un-quiesce — never leave the fleet stopped."""
    _store(fleet / "profiles" / "homelab-admin", claim_age_seconds=CLAIM_TTL / 2)
    module = _load(tmp_path, fleet, drain_timeout=0)
    restarted: list[bool] = []
    module.restart = lambda: (restarted.append(True), 0)[1]

    assert module.main() == 0
    assert restarted == [True]
    assert not list(fleet.rglob("ESTOP"))


def test_the_pause_is_lifted_when_the_restart_fails(tmp_path, fleet) -> None:
    """The `finally` covers the restart itself, not just the wait."""
    module = _load(tmp_path, fleet)

    def _boom():
        raise RuntimeError("systemctl exploded")

    module.restart = _boom
    with pytest.raises(RuntimeError):
        module.main()

    assert not list(fleet.rglob("ESTOP"))


def test_a_store_paused_by_an_operator_stays_paused(tmp_path, fleet) -> None:
    """An existing pause is someone else's; the drain must not lift it."""
    operator_pause = fleet / "profiles" / "splunk-admin" / "ESTOP"
    operator_pause.write_text("{}")
    module = _load(tmp_path, fleet)
    module.restart = lambda: 0

    assert module.main() == 0
    assert [p.parent.name for p in fleet.rglob("ESTOP")] == ["splunk-admin"]


def test_every_store_is_quiesced_before_the_restart(tmp_path, fleet) -> None:
    """The restart must not run against a store still admitting new fires."""
    module = _load(tmp_path, fleet)
    paused_at_restart: list[str] = []
    module.restart = lambda: (
        paused_at_restart.extend(sorted(p.parent.name for p in fleet.rglob("ESTOP"))),
        0,
    )[1]

    assert module.main() == 0
    assert paused_at_restart == ["github-maint", "hermes", "splunk-admin"]


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
