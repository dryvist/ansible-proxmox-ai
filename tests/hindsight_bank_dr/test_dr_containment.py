"""Disabling Hindsight also stops any previously scheduled or running DR work."""

from pathlib import Path

import jinja2
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "roles/hindsight_bank_dr"


def _render_condition(expression, enabled):
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = lambda value: (
        value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}
    )
    return bool(env.compile_expression(expression)(hindsight_bank_dr_enabled=enabled))


@pytest.mark.parametrize("enabled", [False, True])
def test_dr_gate_selects_only_the_requested_branch(enabled):
    tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
    stop = next(task for task in tasks if task.get("ansible.builtin.import_tasks") == "stop.yml")
    configure = next(task for task in tasks if task.get("ansible.builtin.import_tasks") == "configure.yml")
    assert _render_condition(stop["when"], enabled) is not enabled
    assert _render_condition(configure["when"], enabled) is enabled


def test_existing_timers_and_oneshot_services_are_stopped_without_install_or_secrets():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    assert set(defaults["hindsight_bank_dr_jobs"]) == {"hindsight-bank-export", "hindsight-bank-dr-drill"}
    tasks = yaml.safe_load((ROLE / "tasks/stop.yml").read_text())
    stat = next(task for task in tasks if "ansible.builtin.stat" in task)
    units = [task for task in tasks if "ansible.builtin.systemd_service" in task]
    assert len(units) == 2
    timer, service = units
    assert "product(['timer', 'service'])" in stat["loop"]
    assert stat["register"] == "hindsight_bank_dr_existing_units"
    assert all(task["loop"] == "{{ hindsight_bank_dr_existing_units.results }}" for task in units)
    assert timer["when"] == ["item.stat.exists", "item.item.1 == 'timer'"]
    assert service["when"] == ["item.stat.exists", "item.item.1 == 'service'"]
    assert all(task["ansible.builtin.systemd_service"]["state"] == "stopped" for task in units)
    assert timer["ansible.builtin.systemd_service"]["enabled"] is False
    assert "enabled" not in service["ansible.builtin.systemd_service"]
    assert all("include_role" not in str(task) and "secret" not in str(task).lower() for task in tasks)


def test_dr_defaults_follow_the_service_gate():
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters["bool"] = lambda value: (
        value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "on"}
    )
    template = env.from_string(defaults["hindsight_bank_dr_enabled"])
    assert template.render() == "False"
    assert template.render(hindsight_service_enabled=False) == "False"
    assert template.render(hindsight_service_enabled=True) == "True"


@pytest.mark.parametrize(("task_name", "valid", "invalid"), [
    ("Require every DR unit-file check to complete", {"stat": {"exists": True}}, {"unreachable": True}),
    ("Require every requested DR stop to complete", {"changed": True}, {"unreachable": True}),
    ("Require existing DR units to be inactive", {"rc": 3, "stdout": "inactive"}, {"rc": 3, "stdout": "failed"}),
    ("Require existing DR timers to be disabled", {"rc": 1, "stdout": "disabled"}, {"rc": 1, "stdout": "enabled"}),
])
def test_dr_checks_reject_lost_or_unverified_operations(task_name, valid, invalid):
    tasks = yaml.safe_load((ROLE / "tasks/stop.yml").read_text())
    task = next(task for task in tasks if task["name"] == task_name)
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    checks = [env.compile_expression(expr) for expr in task["ansible.builtin.assert"]["that"]]
    assert all(check(item=valid) for check in checks)
    assert not all(check(item=invalid) for check in checks)
    assert not all(check(item={**valid, "unreachable": True}) for check in checks)
    assert not all(check(item={**valid, "failed": True}) for check in checks)
