"""Copies from the Hermes checkout skip a check run in which the checkout itself would move."""
from pathlib import Path

import yaml

TASKS = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent" / "tasks"
GUARD = "not (ansible_check_mode and (hermes_agent_install.changed | default(false)))"


def _checkout_copies():
    for path in sorted(TASKS.glob("*.yml")):
        for task in yaml.safe_load(path.read_text()) or []:
            copy = task.get("ansible.builtin.copy") or {}
            if copy.get("remote_src") and str(copy.get("src", "")).startswith("{{ hermes_agent_install_dir }}"):
                yield path.name, task


def test_every_checkout_copy_skips_a_check_run_that_moves_the_checkout():
    copies = list(_checkout_copies())
    assert len(copies) >= 3
    for name, task in copies:
        when = task.get("when")
        conditions = when if isinstance(when, list) else [when]
        assert GUARD in conditions, (name, task["name"])
