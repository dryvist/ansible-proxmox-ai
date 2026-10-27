"""The drain-before-restart wrapper is installed before any gateway restart notify.

A handler can flush at play end for a host whose block was rescued, so a
notify queued by an earlier task can run before a later install task.
"""

from pathlib import Path

import yaml

from _role_files import role_tasks


ROLE = Path(__file__).resolve().parents[2] / "roles" / "hermes_agent"
INSTALL_NAME = "Deploy the drain-before-restart wrapper for the gateway unit"


def _notifies_gateway(task: dict) -> bool:
    notify = task.get("notify", [])
    if isinstance(notify, str):
        notify = [notify]
    return "Restart hermes-gateway" in notify


def test_wrapper_install_precedes_every_gateway_restart_notify() -> None:
    tasks = role_tasks(ROLE)
    install_index = next(
        index for index, task in enumerate(tasks) if task.get("name") == INSTALL_NAME
    )
    first_notify_index = next(
        index for index, task in enumerate(tasks) if _notifies_gateway(task)
    )
    assert install_index < first_notify_index, (
        "a gateway restart can be notified before its wrapper is installed "
        f"(install task index {install_index}, first notify index {first_notify_index})"
    )


def test_wrapper_install_is_unconditional_and_matches_the_handler_command() -> None:
    install = next(task for task in role_tasks(ROLE) if task.get("name") == INSTALL_NAME)
    handler = next(
        task
        for task in yaml.safe_load((ROLE / "handlers" / "main.yml").read_text())
        if task.get("name") == "Restart hermes-gateway"
    )

    assert "when" not in install, "the wrapper must be installed on every host"
    assert install["ansible.builtin.template"]["dest"] == handler["ansible.builtin.command"]
