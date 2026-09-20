"""The router_settings-row probe (probe-router-settings.yml) must never carry
the database password on the psql argv: argv is visible in /proc/*/cmdline,
`ps`, and audit logs for the probe's lifetime, and `no_log: true` hides only
Ansible's own output, not the process table (promotion-811-review.md
finding 8).

Parsed with yaml.safe_load, never a string grep: a grep for the variable name
would also match it appearing correctly inside `environment:`, which is
exactly the fixed form. Only a parsed check of the argv list specifically
proves the credential left argv.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = REPO_ROOT / "roles/llm_router/tasks/probe-router-settings.yml"


def _probe_task() -> dict:
    tasks = yaml.safe_load(TASK_FILE.read_text(encoding="utf-8"))
    matches = [t for t in tasks if t.get("name") == "Check whether the database already holds a router_settings row"]
    assert len(matches) == 1, "expected exactly one router_settings probe task"
    return matches[0]


def test_task_file_exists():
    assert TASK_FILE.is_file(), TASK_FILE


def test_password_variable_is_absent_from_argv():
    task = _probe_task()
    argv = task["ansible.builtin.command"]["argv"]
    assert not any("llm_router_db_password" in str(arg) for arg in argv), (
        f"llm_router_db_password must not appear on argv (visible in /proc/*/cmdline "
        f"and ps for the probe's lifetime): {argv!r}"
    )


def test_password_is_supplied_via_environment_instead():
    task = _probe_task()
    assert task.get("environment", {}).get("PGPASSWORD") == "{{ llm_router_db_password }}", (
        "expected environment.PGPASSWORD to carry the db password, keeping it off argv"
    )


def test_probe_still_reads_no_log_true():
    # Unrelated to argv, but the whole point of the fix is defense in depth:
    # no_log must remain true regardless of where the credential lives.
    task = _probe_task()
    assert task.get("no_log") is True
