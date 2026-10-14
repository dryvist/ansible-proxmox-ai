"""A gateway older than its inputs is restarted even when no task changed this run.

A handler fires only for a task that reported changed in the current run, so a
run killed before the flush leaves the files in place and its re-run notifies
nothing. The restart handler stamps a marker and finalize compares it with the
files the gateway reads.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from jinja2 import Environment

ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
ENV = Environment()
PROBE = "Restart a gateway whose inputs are newer than its last restart"
FLUSH = "Flush handlers so config/unit changes apply before verification"


def _tasks() -> list[dict]:
    return yaml.safe_load((ROLE / "tasks" / "finalize.yml").read_text())


def _probe() -> dict:
    return next(t for t in _tasks() if t["name"] == PROBE)


def _expr(template: str):
    return ENV.compile_expression(template.strip().removeprefix("{{").removesuffix("}}"))


def _changed(marker: float | None, inputs: list[float | None]) -> bool:
    """Evaluate the probe's own `vars` and `changed_when` for one set of mtimes."""
    stat = lambda mtime: {"stat": {"exists": mtime is not None, "mtime": mtime}}  # noqa: E731
    ctx = {"hermes_agent_gateway_inputs": {"results": [stat(marker), *map(stat, inputs)]}}
    for name, template in _probe()["vars"].items():
        ctx[name] = _expr(template)(**ctx)
    return bool(ENV.compile_expression(_probe()["changed_when"])(**ctx))


def test_probe_notifies_the_gateway_restart_before_the_flush():
    names = [t["name"] for t in _tasks()]
    assert names.index(PROBE) < names.index(FLUSH)
    assert _probe()["notify"] == "Restart hermes-gateway"


def test_the_restart_handler_stamps_the_marker_the_probe_reads():
    handlers = yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
    stamp = next(h for h in handlers if h["name"] == "Record the gateway restart")
    assert stamp["listen"] == "Restart hermes-gateway"
    assert stamp["ansible.builtin.file"]["path"] == "{{ hermes_agent_gateway_restart_marker }}"
    assert stamp["ansible.builtin.file"]["state"] == "touch"


def test_an_input_newer_than_the_marker_restarts_the_gateway():
    assert _changed(100.0, [90.0, 101.5, 80.0])


def test_inputs_older_than_the_marker_restart_nothing():
    assert not _changed(100.0, [90.0, 99.9, 80.0])


def test_no_marker_restarts_the_gateway():
    assert _changed(None, [90.0, 99.9, 80.0])


def test_an_absent_input_is_ignored():
    assert not _changed(100.0, [90.0, None, None])
