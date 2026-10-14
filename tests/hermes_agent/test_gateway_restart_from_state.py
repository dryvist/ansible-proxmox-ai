"""A gateway older than its deployed inputs is restarted even when no task changed this run.

A handler fires only for a task that reported changed in the current run, so a
run killed before the flush leaves the files in place and its re-run notifies
nothing. The finalize probe compares the deployed inputs with the gateway's
start time instead.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment

TASKS = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "tasks"
STARTED = datetime(2026, 10, 14, 2, 0, 0, tzinfo=timezone.utc)
BEFORE, AFTER = STARTED.timestamp() - 3600, STARTED.timestamp() + 3600


def _probe() -> dict:
    tasks = yaml.safe_load((TASKS / "finalize.yml").read_text())
    return next(t for t in tasks if t["name"].startswith("Find deployed gateway inputs"))


def _touch(path: Path, when: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (when, when))


def _run(tmp_path: Path, files: dict[str, float], started: str = "Wed 2026-10-14 02:00:00 UTC") -> str:
    home, install, bin_dir = tmp_path / "home", tmp_path / "lib", tmp_path / "bin"
    unit, drain = tmp_path / "gateway.service", tmp_path / "drain"
    for rel, when in files.items():
        _touch(tmp_path / rel, when)
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text(f"#!/bin/sh\necho '{started}'\n")
    (bin_dir / "systemctl").chmod(0o755)
    script = Environment().from_string(_probe()["ansible.builtin.shell"]).render(
        hermes_agent_hermes_home=home,
        hermes_agent_install_dir=install,
        hermes_agent_cron_drain_restart_script=drain,
    ).replace("/etc/systemd/system/hermes-gateway.service", str(unit))
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    return result.stdout.strip()


def test_probe_notifies_the_gateway_restart_before_the_flush():
    tasks = yaml.safe_load((TASKS / "finalize.yml").read_text())
    names = [t["name"] for t in tasks]
    assert names.index(_probe()["name"]) < names.index(
        "Flush handlers so config/unit changes apply before verification"
    )
    assert _probe()["notify"] == "Restart hermes-gateway"


def test_a_config_newer_than_the_gateway_is_reported(tmp_path):
    assert _run(tmp_path, {"home/config.yaml": AFTER, "home/.env": BEFORE}).endswith("config.yaml")


def test_a_patched_source_file_newer_than_the_gateway_is_reported(tmp_path):
    assert _run(tmp_path, {"lib/cron/scheduler.py": AFTER}).endswith("scheduler.py")


def test_inputs_older_than_the_gateway_report_nothing(tmp_path):
    files = {"home/config.yaml": BEFORE, "home/.env": BEFORE, "lib/run_agent.py": BEFORE}
    assert _run(tmp_path, files) == ""


def test_a_lazy_dependency_installed_into_the_venv_is_ignored(tmp_path):
    assert _run(tmp_path, {"lib/venv/lib/site.py": AFTER, "lib/run_agent.py": BEFORE}) == ""


def test_a_gateway_that_never_started_reports_nothing(tmp_path):
    assert _run(tmp_path, {"home/config.yaml": AFTER}, started="") == ""
