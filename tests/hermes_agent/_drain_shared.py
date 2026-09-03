"""Shared harness for the drain-before-restart wrapper's tests.

Split out when test_cron_drain_before_restart.py crossed its token budget. The
two suites that use it divide by concern: drain mechanics (what is in flight,
how long to wait, when to restart) and sentinel ownership (whose pause is it,
and may this run clear it).

Nothing here substitutes anything on the module under test. Claims come from
real job stores on disk in the layout the guest uses, and the real `restart()`
runs against a `systemctl` shim on PATH that records the argv it received and
the sentinels standing at that instant — so the assertions read the shipped
subprocess call rather than a double, and gutting a function does not leave
them green.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jinja2 import Environment

from _role_files import role_defaults

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
TEMPLATE = ROLE / "templates" / "hermes-cron-drain-restart.py.j2"

DEFAULTS = role_defaults(ROLE)
HERMES_USER = DEFAULTS["hermes_agent_user"]
CLAIM_TTL = int(DEFAULTS["hermes_agent_cron_fire_claim_ttl_seconds"])

# Records the restart invocation and the sentinels standing at that moment, so
# the assertions read the real subprocess call the shipped `restart()` makes.
# DRAIN_TEST_RM_SENTINELS stands in for an operator clearing them by hand: this
# is the one moment the wrapper is between engaging and releasing them.
SYSTEMCTL_SHIM = """#!/bin/sh
if [ "$1" = "$DRAIN_TEST_FAIL_ON" ]; then exit 1; fi
if [ "$1" = "restart" ]; then
  { echo "$*"; find "$DRAIN_TEST_FLEET" -name ESTOP; } > "$DRAIN_TEST_LOG"
  if [ -n "$DRAIN_TEST_RM_SENTINELS" ]; then
    find "$DRAIN_TEST_FLEET" -name ESTOP -exec rm {} +
  fi
fi
exit 0
"""


def render(hermes_home: Path, *, drain_timeout: int) -> str:
    """Render the template the role deploys, with a test-sized drain bound."""
    return Environment(keep_trailing_newline=True).from_string(
        TEMPLATE.read_text()
    ).render(
        ansible_managed="Ansible managed",
        hermes_agent_hermes_home=str(hermes_home),
        hermes_agent_user=HERMES_USER,
        hermes_agent_cron_fire_claim_ttl_seconds=CLAIM_TTL,
        hermes_agent_cron_drain_timeout_seconds=drain_timeout,
        hermes_agent_cron_drain_poll_seconds=0,
    )


def load(tmp_path: Path, hermes_home: Path, *, drain_timeout: int = 0):
    """Import the rendered script as a module so its functions are callable."""
    path = tmp_path / "drain_restart.py"
    path.write_text(render(hermes_home, drain_timeout=drain_timeout))
    spec = importlib.util.spec_from_file_location("hermes_drain_restart", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def store(home: Path, *, claim_age_seconds: float | None) -> Path:
    """A cron store holding one job, laid out as the guest lays it out."""
    (home / "cron").mkdir(parents=True)
    job: dict = {"id": "j1", "name": "some-job", "fire_claim": None}
    if claim_age_seconds is not None:
        at = datetime.now(timezone.utc) - timedelta(seconds=claim_age_seconds)
        job["fire_claim"] = {"at": at.isoformat(), "by": "host:abc"}
    (home / "cron" / "jobs.json").write_text(json.dumps([job]))
    return home


def converge_sentinel(module, home: Path, *, age_seconds: float) -> Path:
    """A sentinel of the shape this wrapper writes, stamped `age_seconds` ago."""
    sentinel = home / "ESTOP"
    sentinel.write_text(json.dumps({
        "owner": module.SENTINEL_OWNER,
        "engaged_at": (
            datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        ).isoformat(),
        "reason": "draining hermes-gateway for restart",
    }))
    return sentinel


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
def fleet(tmp_path: Path) -> Path:
    """A default store plus two profile stores, one of them job-less."""
    home = tmp_path / "hermes"
    store(home, claim_age_seconds=None)
    store(home / "profiles" / "splunk-admin", claim_age_seconds=None)
    (home / "profiles" / "github-maint").mkdir(parents=True)
    return home


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
