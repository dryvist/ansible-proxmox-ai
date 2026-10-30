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
    stop = next(task for task in tasks if "ansible.builtin.systemd_service" in task)
    assert "product(['timer', 'service'])" in stat["loop"]
    assert stat["register"] == "hindsight_bank_dr_existing_units"
    assert stop["loop"] == "{{ hindsight_bank_dr_existing_units.results }}"
    assert stop["when"] == "item.stat.exists"
    assert stop["ansible.builtin.systemd_service"]["state"] == "stopped"
    assert stop["ansible.builtin.systemd_service"]["enabled"] is False
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
